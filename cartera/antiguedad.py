"""
Antigüedad de saldos por nota, como "Cobro a clientes 2026" del área: lo que
el cliente debe en cada empresa, partido en las notas que lo forman, con los
días desde la fecha de cada nota y su rango (1-15, 16-30, 31-45, 46-89, 90+).

Partidas (lo que se debe): el saldo al corte (una sola, con la fecha del
corte), cada venta (salida, por empresa del lote) y cada cargo. Se descuentan:
  1. lo que Cartera aplicó de cada pago a una nota (AplicacionPago);
  2. después, en orden de fecha, lo no aplicado de los pagos, los abonos y el
     saldo a favor al corte, siempre de la partida más antigua con pendiente.
Lo que sobra queda como saldo a favor. La suma de lo pendiente menos el saldo
a favor es el saldo de cartera/saldos.py.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .models import AjusteCliente, AplicacionPago, PagoCliente, SaldoInicialCliente
from .ventas import empresa_de, filtro_empresa, importe, lineas_venta

CERO = Decimal('0')
INICIAL = 'inicial'
VENTA = 'venta'
CARGO = 'cargo'

# (etiqueta, último día del rango); el último no tiene tope.
RANGOS = (('1-15', 15), ('16-30', 30), ('31-45', 45), ('46-89', 89), ('90 O MÁS', None))


def rango(dias):
    """Índice del rango de días vencidos (el día de la nota cuenta en 1-15)."""
    for indice, (_, tope) in enumerate(RANGOS):
        if tope is None or dias <= tope:
            return indice
    return len(RANGOS) - 1


@dataclass
class Partida:
    tipo: str  # INICIAL | VENTA | CARGO
    nota: str
    fecha: date
    importe: Decimal
    orden: tuple
    salida_id: int | None = None
    folio: str = ''
    # Lo aplicado a esta nota a mano desde los pagos (lo que limita aplicar más).
    aplicado: Decimal = CERO
    # Cada descuento por separado, en el orden en que se aplicó.
    descuentos: list = field(default_factory=list)

    @property
    def pendiente(self):
        return self.importe - sum(self.descuentos, CERO)

    def descontar(self, monto):
        """Descuenta hasta `monto`; devuelve lo que no cupo."""
        parte = min(monto, self.pendiente)
        if parte > 0:
            self.descuentos.append(parte)
        return monto - max(parte, CERO)


@dataclass
class CuentaEmpresa:
    """Lo del cliente en una empresa."""
    partidas: list = field(default_factory=list)
    a_favor: Decimal = CERO

    @property
    def saldo(self):
        return sum((p.pendiente for p in self.partidas), CERO) - self.a_favor


def cuentas(clientes=None, empresa_id=None, hasta=None, excluir_pago=None):
    """
    {(cliente_id, empresa_id): CuentaEmpresa} con los movimientos hasta `hasta`.
    `excluir_pago`: el pago que se está editando, como si no existiera.
    """
    resultado = defaultdict(CuentaEmpresa)
    # Créditos que se descuentan de lo más antiguo: (cliente, empresa) → [(fecha, orden, monto)].
    creditos = defaultdict(list)

    def filtrar(queryset, campo_cliente='cliente_id'):
        if clientes is not None:
            queryset = queryset.filter(**{f'{campo_cliente}__in': clientes})
        if empresa_id is not None:
            queryset = queryset.filter(empresa_id=empresa_id)
        if hasta is not None:
            queryset = queryset.filter(fecha__lte=hasta)
        return queryset

    for inicial in filtrar(SaldoInicialCliente.objects.all()):
        clave = (inicial.cliente_id, inicial.empresa_id)
        if inicial.monto > 0:
            resultado[clave].partidas.append(Partida(
                tipo=INICIAL, nota=f'SALDO AL {inicial.fecha:%d/%m/%Y}', fecha=inicial.fecha,
                importe=inicial.monto, orden=(inicial.fecha, 0, 0),
            ))
        elif inicial.monto < 0:
            creditos[clave].append((inicial.fecha, (0, 0), -inicial.monto))

    ventas = lineas_venta(clientes=clientes, hasta=hasta)
    if empresa_id is not None:
        ventas = ventas.filter(filtro_empresa(empresa_id))
    por_nota = {}
    for linea in ventas:
        monto = importe(linea)
        if monto is None:
            continue
        salida = linea.salida
        clave = (salida.cliente_id, empresa_de(linea).id)
        partida = por_nota.get((clave, salida.id))
        if partida is None:
            partida = por_nota[(clave, salida.id)] = Partida(
                tipo=VENTA, nota=nombre_nota(salida), fecha=salida.fecha,
                importe=CERO, orden=(salida.fecha, 1, salida.id), salida_id=salida.id,
                folio=salida.folio_de_salida or '',
            )
            resultado[clave].partidas.append(partida)
        partida.importe += monto

    for ajuste in filtrar(AjusteCliente.objects.filter(cancelado=False)):
        clave = (ajuste.cliente_id, ajuste.empresa_id)
        if ajuste.naturaleza == AjusteCliente.CARGO:
            resultado[clave].partidas.append(Partida(
                tipo=CARGO, nota=ajuste.concepto, fecha=ajuste.fecha, importe=ajuste.monto,
                orden=(ajuste.fecha, 1, ajuste.creado.timestamp()),
            ))
        else:
            creditos[clave].append((ajuste.fecha, (1, ajuste.creado.timestamp()), ajuste.monto))

    for cuenta in resultado.values():
        cuenta.partidas.sort(key=lambda p: p.orden)

    pagos = filtrar(PagoCliente.objects.filter(cancelado=False, cliente__isnull=False))
    if excluir_pago is not None:
        pagos = pagos.exclude(pk=excluir_pago)
    aplicado_por_pago = defaultdict(lambda: CERO)
    aplicaciones = (
        AplicacionPago.objects.filter(pago__in=pagos)
        .select_related('pago').order_by('pago__fecha', 'pago__creado', 'id')
    )
    for aplicacion in aplicaciones:
        pago = aplicacion.pago
        cuenta = resultado.get((pago.cliente_id, pago.empresa_id))
        destino = None
        if cuenta is not None:
            tipo = VENTA if aplicacion.salida_id else INICIAL
            destino = next(
                (p for p in cuenta.partidas if p.tipo == tipo and p.salida_id == aplicacion.salida_id), None,
            )
        if destino is None:
            continue  # La nota ya no está (o es posterior a `hasta`): el pago va a lo más antiguo.
        destino.aplicado += aplicacion.monto
        sobrante = destino.descontar(aplicacion.monto)
        aplicado_por_pago[pago.id] += aplicacion.monto - sobrante
    for pago in pagos:
        libre = pago.monto - aplicado_por_pago[pago.id]
        if libre > 0:
            creditos[(pago.cliente_id, pago.empresa_id)].append((pago.fecha, (1, pago.creado.timestamp()), libre))

    for clave, lista in creditos.items():
        cuenta = resultado[clave]
        for _, _, monto in sorted(lista, key=lambda c: (c[0], c[1])):
            for partida in cuenta.partidas:
                if monto <= 0:
                    break
                monto = partida.descontar(monto)
            cuenta.a_favor += monto
    return resultado


def notas_pendientes(cliente_id, empresa_id, hasta=None, excluir_pago=None):
    """Notas a las que se puede aplicar un pago (saldo al corte y ventas), las más antiguas primero."""
    cuenta = cuentas(clientes=[cliente_id], empresa_id=empresa_id, hasta=hasta, excluir_pago=excluir_pago).get(
        (cliente_id, empresa_id), CuentaEmpresa(),
    )
    return [p for p in cuenta.partidas if p.tipo in (INICIAL, VENTA)]


def reporte(fecha, clientes=None, empresa_id=None):
    """
    Antigüedad al `fecha`: por cliente (con saldo distinto de cero) y empresa,
    sus notas con pendiente, totales por rango y el saldo en conjunto.
    """
    from inventario.models import Cliente, Empresa

    datos = cuentas(clientes=clientes, empresa_id=empresa_id, hasta=fecha)
    nombres = dict(Cliente.objects.filter(pk__in={c for c, _ in datos}).values_list('id', 'nombre'))
    empresas = {e.id: e for e in Empresa.objects.filter(pk__in={e for _, e in datos})}

    def vacios():
        return [CERO] * len(RANGOS)

    por_cliente = defaultdict(list)
    totales_empresa = defaultdict(lambda: {'por_rango': vacios(), 'a_favor': CERO, 'saldo': CERO})
    for (cliente_id, emp_id), cuenta in datos.items():
        if cuenta.saldo == 0 and not any(p.pendiente for p in cuenta.partidas):
            continue
        notas = []
        por_rango = vacios()
        for partida in cuenta.partidas:
            if partida.pendiente == 0:
                continue
            dias = (fecha - partida.fecha).days
            indice = rango(dias)
            por_rango[indice] += partida.pendiente
            notas.append({'partida': partida, 'dias': dias, 'rango': indice})
        total = totales_empresa[emp_id]
        total['por_rango'] = [a + b for a, b in zip(total['por_rango'], por_rango)]
        total['a_favor'] += cuenta.a_favor
        total['saldo'] += cuenta.saldo
        por_cliente[cliente_id].append({
            'empresa': empresas[emp_id], 'notas': notas, 'por_rango': por_rango,
            'a_favor': cuenta.a_favor, 'saldo': cuenta.saldo,
        })

    filas = []
    for cliente_id, bloques in por_cliente.items():
        bloques.sort(key=lambda b: b['empresa'].nombre)
        filas.append({
            'cliente': {'id': cliente_id, 'nombre': nombres[cliente_id]},
            'empresas': bloques,
            'por_rango': [sum(v, CERO) for v in zip(*(b['por_rango'] for b in bloques))],
            'conjunto': sum((b['saldo'] for b in bloques), CERO),
        })
    filas.sort(key=lambda f: f['cliente']['nombre'])
    return {
        'fecha': fecha,
        'clientes': filas,
        'empresas': [
            {'empresa': empresas[e], **totales_empresa[e]}
            for e in sorted(totales_empresa, key=lambda e: empresas[e].nombre)
        ],
    }


def nombre_nota(salida):
    return salida.notas or f'Salida {salida.folio_de_salida}'


def partes_aplicadas(pago):
    """[(nota, monto)] de lo aplicado a mano (precargar `aplicaciones__salida` en listas)."""
    return [
        (f'Nota {nombre_nota(a.salida)}' if a.salida_id else 'Saldo al corte', a.monto)
        for a in pago.aplicaciones.all()
    ]


def texto_aplicacion(partes, monto_pago):
    """'Nota 123 $1,000.00; resto a lo más antiguo' (vacío si todo va a lo más antiguo)."""
    if not partes:
        return ''
    texto = '; '.join(f'{nota} ${monto:,.2f}' for nota, monto in partes)
    if sum((m for _, m in partes), CERO) < monto_pago:
        texto += '; resto a lo más antiguo'
    return texto


def _dinero(valor):
    return str(Decimal(valor).quantize(Decimal('0.01')))


def serializar(datos):
    def bloque(b):
        return {
            'empresa': {'id': b['empresa'].id, 'nombre': b['empresa'].nombre},
            'notas': [
                {
                    'tipo': n['partida'].tipo, 'salida_id': n['partida'].salida_id, 'nota': n['partida'].nota,
                    'folio': n['partida'].folio, 'fecha': n['partida'].fecha.isoformat(), 'dias': n['dias'],
                    'rango': n['rango'], 'importe': _dinero(n['partida'].importe),
                    'descontado': _dinero(n['partida'].importe - n['partida'].pendiente),
                    'pendiente': _dinero(n['partida'].pendiente),
                }
                for n in b['notas']
            ],
            'por_rango': [_dinero(v) for v in b['por_rango']],
            'a_favor': _dinero(b['a_favor']),
            'saldo': _dinero(b['saldo']),
        }

    return {
        'fecha': datos['fecha'].isoformat(),
        'rangos': [etiqueta for etiqueta, _ in RANGOS],
        'clientes': [
            {
                'cliente': f['cliente'], 'empresas': [bloque(b) for b in f['empresas']],
                'por_rango': [_dinero(v) for v in f['por_rango']], 'conjunto': _dinero(f['conjunto']),
            }
            for f in datos['clientes']
        ],
        'empresas': [
            {
                'empresa': {'id': e['empresa'].id, 'nombre': e['empresa'].nombre},
                'por_rango': [_dinero(v) for v in e['por_rango']],
                'a_favor': _dinero(e['a_favor']), 'saldo': _dinero(e['saldo']),
            }
            for e in datos['empresas']
        ],
    }


# ---------------------------------------------------------------- Excel
FORMATO = '#,##0.00;[Red]-#,##0.00'
AZUL = '1F4E78'
# Con más descuentos que estos en una nota, la fórmula los lleva sumados.
MAX_DESCUENTOS_FORMULA = 25


def _texto(celda, valor):
    """Texto capturado por usuarios: siempre cadena, nunca fórmula."""
    celda.value = valor
    celda.data_type = 's'
    return celda


def _monto(partida):
    """Como en el Excel del área: importe de la nota menos cada pago descontado."""
    if not partida.descuentos:
        return float(partida.importe)
    partes = partida.descuentos
    if len(partes) > MAX_DESCUENTOS_FORMULA:
        partes = [sum(partes, CERO)]
    return '=' + format(partida.importe, 'f') + ''.join('-' + format(d, 'f') for d in partes)


def construir_excel(datos):
    """
    Hoja "COBRO A CLIENTES" como la del área (un bloque por cliente y empresa:
    días = fecha del reporte − fecha de la nota, el monto en la columna de su
    rango, totales y saldo en conjunto) y una hoja RESUMEN por cliente y empresa.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    resumen = wb.active
    resumen.title = 'RESUMEN'
    ws = wb.create_sheet('COBRO A CLIENTES')
    azul = PatternFill('solid', fgColor=AZUL)
    claro = PatternFill('solid', fgColor='DDEBF7')
    blanco = Font(bold=True, color='FFFFFF')
    negrita = Font(bold=True)
    fecha = datos['fecha']
    columnas_rango = [get_column_letter(6 + i) for i in range(len(RANGOS))]  # F..J
    ultima_col = columnas_rango[-1]

    fila = 2
    for cliente in datos['clientes']:
        totales_bloque = []
        for b in cliente['empresas']:
            ws.cell(fila, 3, b['empresa'].nombre).font = Font(bold=True, size=12)
            ws.merge_cells(f'C{fila}:{ultima_col}{fila}')
            ws.cell(fila + 1, 3, 'SALDO A LA FECHA:').font = negrita
            celda_fecha = ws.cell(fila + 1, 5, fecha)
            celda_fecha.number_format = 'DD/MM/YYYY'
            celda_fecha.font = negrita
            ref_fecha = f'$E${fila + 1}'
            _texto(ws.cell(fila + 2, 3), f"CLIENTE: {cliente['cliente']['nombre']}").font = negrita
            ws.cell(fila + 3, 6, 'DÍAS VENCIDOS').alignment = Alignment(horizontal='center')
            ws.cell(fila + 3, 6).font = negrita
            ws.merge_cells(f'F{fila + 3}:{ultima_col}{fila + 3}')
            encabezados = ['DÍAS DE ANTIGÜEDAD', 'NOTA/FACT', 'FECHA', 'MONTO'] + [e for e, _ in RANGOS]
            for col, titulo in enumerate(encabezados, start=2):
                celda = ws.cell(fila + 4, col, titulo)
                celda.font = blanco
                celda.fill = azul
                celda.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            primera = fila + 5
            r = primera
            for n in b['notas']:
                partida = n['partida']
                ws.cell(r, 2, f'={ref_fecha}-D{r}').number_format = '0'
                _texto(ws.cell(r, 3), partida.nota)
                ws.cell(r, 4, partida.fecha).number_format = 'DD/MM/YYYY'
                ws.cell(r, 5, _monto(partida)).number_format = FORMATO
                ws.cell(r, 6 + n['rango'], f'=+E{r}').number_format = FORMATO
                r += 1
            if b['a_favor']:
                _texto(ws.cell(r, 3), 'SALDO A FAVOR')
                ws.cell(r, 5, -float(b['a_favor'])).number_format = FORMATO
                r += 1
            ultima = max(r - 1, primera)
            ws.cell(r, 3, 'TOTALES').font = negrita
            for letra in ['E'] + columnas_rango:
                celda = ws[f'{letra}{r}']
                celda.value = f'=SUM({letra}{primera}:{letra}{ultima})'
                celda.number_format = FORMATO
                celda.font = negrita
                celda.fill = claro
            totales_bloque.append(r)
            fila = r + 2
        # Saldo en conjunto del cliente (todas sus empresas).
        ws.cell(fila, 3, 'SALDO EN CONJUNTO')
        for letra in ['C', 'E'] + columnas_rango:
            celda = ws[f'{letra}{fila}']
            if letra != 'C':
                celda.value = '=' + '+'.join(f'{letra}{t}' for t in totales_bloque)
                celda.number_format = FORMATO
            celda.font = blanco
            celda.fill = azul
        fila += 4

    for col, ancho in zip('ABCDEFGHIJ', (2, 12, 30, 12, 18, 16, 16, 16, 16, 16)):
        ws.column_dimensions[col].width = ancho

    # RESUMEN: una fila por cliente y empresa con su saldo por rango.
    n = len(RANGOS)
    letra_ultimo_rango = get_column_letter(2 + n)
    letra_favor = get_column_letter(3 + n)
    resumen.append([f'ANTIGÜEDAD DE SALDOS AL {fecha:%d/%m/%Y}'])
    resumen['A1'].font = Font(bold=True, size=12)
    resumen.append(['CLIENTE', 'EMPRESA'] + [e for e, _ in RANGOS] + ['SALDO A FAVOR', 'SALDO'])
    for col in range(1, n + 5):
        celda = resumen.cell(2, col)
        celda.font = blanco
        celda.fill = azul
        celda.alignment = Alignment(horizontal='center', wrap_text=True)
    fila = 3
    for cliente in datos['clientes']:
        for b in cliente['empresas']:
            _texto(resumen.cell(fila, 1), cliente['cliente']['nombre'])
            _texto(resumen.cell(fila, 2), b['empresa'].nombre)
            for i, valor in enumerate(b['por_rango']):
                resumen.cell(fila, 3 + i, float(valor)).number_format = FORMATO
            resumen.cell(fila, 3 + n, float(b['a_favor'])).number_format = FORMATO
            resumen.cell(fila, 4 + n, f'=SUM(C{fila}:{letra_ultimo_rango}{fila})-{letra_favor}{fila}').number_format = FORMATO
            fila += 1
    ultima = max(fila - 1, 3)
    for empresa in datos['empresas']:
        nombre = empresa['empresa'].nombre
        _texto(resumen.cell(fila, 1), f'TOTAL {nombre}').font = negrita
        for col in range(3, n + 5):
            letra = get_column_letter(col)
            celda = resumen.cell(fila, col, f'=SUMIF($B$3:$B${ultima},"{nombre}",{letra}$3:{letra}${ultima})')
            celda.number_format = FORMATO
            celda.font = negrita
        fila += 1
    resumen.cell(fila, 1, 'TOTAL EN CONJUNTO')
    for col in range(1, n + 5):
        celda = resumen.cell(fila, col)
        if col >= 3:
            letra = get_column_letter(col)
            celda.value = f'=SUM({letra}3:{letra}{ultima})'
            celda.number_format = FORMATO
        celda.font = blanco
        celda.fill = azul
    for col, ancho in zip('ABCDEFGHI', (42, 20, 16, 16, 16, 16, 16, 16, 18)):
        resumen.column_dimensions[col].width = ancho
    resumen.freeze_panes = 'C3'
    return wb
