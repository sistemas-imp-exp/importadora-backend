"""
Control mensual por empresa, como las hojas "IMPORTADORA OCTUBRE 2026" /
"SELECTOS OCTUBRE 2026" de docs/CARTERA/CARTERA.xlsx: por cliente, el saldo al
cierre del mes anterior, las ventas y los pagos del mes y el saldo actual; al
lado, la lista de pagos (tipo, fecha, cliente, cantidad) y la de ventas (nota,
fecha, cliente, cantidad) del mes.

Los ajustes (cargos − abonos, incluidos los traspasos entre empresas) no
existen en la hoja del área pero mueven el saldo: van en su propia columna
para que el saldo actual cuadre con el estado de cuenta.
"""
import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

from django.db.models import Sum
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo

from inventario.models import Empresa

from .models import AjusteCliente, PagoCliente
from .saldos import fecha_corte, saldos_por_cliente
from .ventas import empresa_de, importe, lineas_venta

CERO = Decimal('0')
MESES = ['ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO', 'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE',
         'NOVIEMBRE', 'DICIEMBRE']
# Como en el control del área: separador de miles, dos decimales y guion en cero.
FORMATO = '_-* #,##0.00_-;\\-* #,##0.00_-;_-* "-"??_-;_-@_-'
AZUL = '4472C4'


class MesInvalido(ValueError):
    pass


def periodo(anio, mes):
    """(primer día, último día) del mes; no antes del corte de la carga inicial."""
    inicio = date(anio, mes, 1)
    fin = date(anio, mes, calendar.monthrange(anio, mes)[1])
    corte = fecha_corte()
    if corte and inicio <= corte:
        raise MesInvalido(f'El sistema lleva la cartera desde el corte del {corte:%d/%m/%Y}: '
                          f'elige un mes posterior.')
    return inicio, fin


def control_mensual(anio, mes):
    """
    [{empresa, filas: [{cliente, anterior, ventas, pagos, ajustes, actual}], pagos: [...], ventas: [...]}]
    por empresa (por nombre). Solo clientes con saldo anterior o con
    movimientos en el mes.
    """
    inicio, fin = periodo(anio, mes)
    anteriores = saldos_por_cliente(hasta=inicio - timedelta(days=1))
    por_empresa = defaultdict(lambda: {
        'clientes': defaultdict(lambda: {'ventas': CERO, 'pagos': CERO, 'ajustes': CERO}),
        'pagos': [], 'ventas': [],
    })
    nombres = {}

    for linea in lineas_venta(hasta=fin).filter(salida__fecha__gte=inicio).select_related('salida__cliente'):
        monto = importe(linea)
        if monto is None:
            continue
        empresa = empresa_de(linea)
        cliente = linea.salida.cliente
        nombres[cliente.id] = cliente.nombre
        datos = por_empresa[empresa.id]
        datos['clientes'][cliente.id]['ventas'] += monto
        datos['ventas'].append({
            'salida_id': linea.salida_id, 'nota': linea.salida.notas, 'fecha': linea.salida.fecha,
            'cliente_id': cliente.id, 'cantidad': monto,
        })

    pagos = (
        PagoCliente.objects.filter(cancelado=False, cliente__isnull=False, fecha__range=(inicio, fin))
        .select_related('cliente').order_by('fecha', 'creado')
    )
    for pago in pagos:
        nombres[pago.cliente_id] = pago.cliente.nombre
        datos = por_empresa[pago.empresa_id]
        datos['clientes'][pago.cliente_id]['pagos'] += pago.monto
        datos['pagos'].append({'tipo': pago.tipo, 'fecha': pago.fecha, 'cliente_id': pago.cliente_id, 'cantidad': pago.monto})

    ajustes = (
        AjusteCliente.objects.filter(cancelado=False, fecha__range=(inicio, fin))
        .values('cliente_id', 'cliente__nombre', 'empresa_id', 'naturaleza').annotate(total=Sum('monto'))
    )
    for a in ajustes:
        nombres[a['cliente_id']] = a['cliente__nombre']
        signo = 1 if a['naturaleza'] == AjusteCliente.CARGO else -1
        por_empresa[a['empresa_id']]['clientes'][a['cliente_id']]['ajustes'] += signo * a['total']

    # Clientes con saldo anterior aunque no se movieran en el mes.
    from inventario.models import Cliente
    con_saldo = {c_id for c_id, saldos in anteriores.items() if any(saldos.values())}
    nombres.update(Cliente.objects.filter(pk__in=con_saldo - set(nombres)).values_list('id', 'nombre'))

    resultado = []
    for empresa in Empresa.objects.order_by('nombre'):
        datos = por_empresa.get(empresa.id, {'clientes': {}, 'pagos': [], 'ventas': []})
        ids = set(datos['clientes']) | {c for c in con_saldo if anteriores[c][empresa.id]}
        filas = []
        for cliente_id in sorted(ids, key=lambda c: nombres[c]):
            mov = datos['clientes'].get(cliente_id, {'ventas': CERO, 'pagos': CERO, 'ajustes': CERO})
            anterior = anteriores[cliente_id][empresa.id]
            filas.append({
                'cliente': {'id': cliente_id, 'nombre': nombres[cliente_id]},
                'anterior': anterior, 'ventas': mov['ventas'], 'pagos': mov['pagos'], 'ajustes': mov['ajustes'],
                'actual': anterior + mov['ventas'] - mov['pagos'] + mov['ajustes'],
            })
        # Las ventas se listan por nota (una fila por salida), como en el control.
        ventas = {}
        for v in datos['ventas']:
            fila = ventas.setdefault(v['salida_id'], {**v, 'cantidad': CERO})
            fila['cantidad'] += v['cantidad']
        resultado.append({
            'empresa': {'id': empresa.id, 'nombre': empresa.nombre},
            'filas': filas,
            'pagos': [{**p, 'cliente': nombres[p['cliente_id']]} for p in datos['pagos']],
            'ventas': [{**v, 'cliente': nombres[v['cliente_id']]}
                       for v in sorted(ventas.values(), key=lambda v: (v['fecha'], v['salida_id']))],
        })
    return resultado


def totales(empresa):
    return {campo: sum((f[campo] for f in empresa['filas']), CERO)
            for campo in ('anterior', 'ventas', 'pagos', 'ajustes', 'actual')}


def serializar(control, anio, mes):
    def dinero(valor):
        return str(Decimal(valor).quantize(Decimal('0.01')))

    return {
        'anio': anio, 'mes': mes,
        'empresas': [
            {
                'empresa': e['empresa'],
                'totales': {k: dinero(v) for k, v in totales(e).items()},
                'filas': [{**f, **{k: dinero(f[k]) for k in ('anterior', 'ventas', 'pagos', 'ajustes', 'actual')}}
                          for f in e['filas']],
                'pagos': [{'tipo': p['tipo'], 'fecha': p['fecha'].isoformat(), 'cliente': p['cliente'],
                           'cantidad': dinero(p['cantidad'])} for p in e['pagos']],
                'ventas': [{'nota': v['nota'], 'fecha': v['fecha'].isoformat(), 'cliente': v['cliente'],
                            'cantidad': dinero(v['cantidad'])} for v in e['ventas']],
            }
            for e in control
        ],
    }


# ---------------------------------------------------------------- Excel
def _titulo_hoja(nombre, anio, mes):
    corto = 'SELECTOS' if 'SELECTOS' in nombre.upper() else nombre.upper()
    return f'{corto} {MESES[mes - 1]} {anio}'[:31]


def _texto(celda, valor):
    """Texto capturado por usuarios: siempre cadena, nunca fórmula."""
    celda.value = valor
    if isinstance(valor, str):
        celda.data_type = 's'
    return celda


def construir_excel(control, anio, mes):
    wb = Workbook()
    wb.remove(wb.active)
    azul = PatternFill('solid', fgColor=AZUL)
    blanco = Font(bold=True, color='FFFFFF', size=12)
    cierre = date(anio, mes, 1) - timedelta(days=1)
    for indice, empresa in enumerate(control, start=1):
        ws = wb.create_sheet(_titulo_hoja(empresa['empresa']['nombre'], anio, mes))
        nombre_mes = f'{MESES[mes - 1]} {anio}'
        ws['A1'] = f"SALDO DE CLIENTES DE {empresa['empresa']['nombre']}"
        ws.merge_cells('A1:F1')
        ws['H1'] = f'PAGOS DE CLIENTE {nombre_mes}'
        ws.merge_cells('H1:K1')
        ws['M1'] = f'VENTAS A CLIENTES {nombre_mes}'
        ws.merge_cells('M1:P1')
        for celda in ('A1', 'H1', 'M1'):
            ws[celda].font = blanco
            ws[celda].fill = azul
            ws[celda].alignment = Alignment(horizontal='center')

        # Saldos por cliente (tabla con estilo como la del control).
        encabezados = ['DESCRIPCIÓN', f'SALDO AL {cierre:%d/%m/%Y}', 'VENTAS', 'PAGOS', 'AJUSTES', 'SALDO ACTUAL']
        for col, titulo in enumerate(encabezados, start=1):
            ws.cell(2, col, titulo)
        fila = 3
        for f in empresa['filas']:
            _texto(ws.cell(fila, 1), f['cliente']['nombre'])
            for col, campo in ((2, 'anterior'), (3, 'ventas'), (4, 'pagos'), (5, 'ajustes')):
                ws.cell(fila, col, float(f[campo])).number_format = FORMATO
            ws.cell(fila, 6, f'=B{fila}+C{fila}-D{fila}+E{fila}').number_format = FORMATO
            fila += 1
        ultima = max(fila - 1, 3)
        if empresa['filas']:
            ws.add_table(_tabla(f'Saldos{indice}', f'A2:F{ultima}'))
        ws.cell(ultima + 1, 1, 'TOTALES').font = Font(bold=True)
        for col, letra in enumerate('BCDEF', start=2):
            celda = ws.cell(ultima + 1, col, f'=SUM({letra}3:{letra}{ultima})')
            celda.number_format = FORMATO
            celda.font = Font(bold=True)

        # Pagos del mes (H:K) y ventas del mes (M:P).
        for col, titulo in zip('HIJK', ('TIPO', 'FECHA', 'CLIENTE', 'CANTIDAD')):
            ws[f'{col}2'] = titulo
        for i, p in enumerate(empresa['pagos'], start=3):
            ws.cell(i, 8, p['tipo'])
            ws.cell(i, 9, p['fecha']).number_format = 'DD/MM/YYYY'
            _texto(ws.cell(i, 10), p['cliente'])
            ws.cell(i, 11, float(p['cantidad'])).number_format = FORMATO
        if empresa['pagos']:
            ws.add_table(_tabla(f'Pagos{indice}', f'H2:K{2 + len(empresa["pagos"])}'))
        for col, titulo in zip('MNOP', ('NOTA', 'FECHA', 'CLIENTE', 'CANTIDAD')):
            ws[f'{col}2'] = titulo
        for i, v in enumerate(empresa['ventas'], start=3):
            _texto(ws.cell(i, 13), v['nota'])
            ws.cell(i, 14, v['fecha']).number_format = 'DD/MM/YYYY'
            _texto(ws.cell(i, 15), v['cliente'])
            ws.cell(i, 16, float(v['cantidad'])).number_format = FORMATO
        if empresa['ventas']:
            ws.add_table(_tabla(f'Ventas{indice}', f'M2:P{2 + len(empresa["ventas"])}'))

        for col, ancho in zip('ABCDEFGHIJKLMNOP', (42, 18, 16, 16, 14, 18, 2, 7, 12, 34, 15, 2, 12, 12, 34, 15)):
            ws.column_dimensions[col].width = ancho
        ws.freeze_panes = 'B3'
        ws.cell(ultima + 3, 1, 'Ajustes = cargos − abonos del mes (incluye traspasos entre empresas).').font = Font(
            italic=True, color='808080')
    buffer = BytesIO()
    wb.save(buffer)
    return buffer


def _tabla(nombre, ref):
    tabla = Table(displayName=nombre, ref=ref)
    tabla.tableStyleInfo = TableStyleInfo(name='TableStyleMedium2', showRowStripes=True)
    return tabla
