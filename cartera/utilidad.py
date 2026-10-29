"""
Utilidad real de Cartera, como la hoja "Registro" del área (Utilidad real
10.-OCTUBRE.xlsx): una fila por línea de venta con lo ya registrado en
Inventario (fecha, lote, nota, proveedor, cliente, producto, kilos, precio,
venta) más el costo unitario que Cartera asigna rastreando la factura.

- Costo: el capturado (CostoVenta) o, si falta, el del lote como propuesta.
  Una venta en resguardo no tiene lote: sin captura queda sin costo.
- Utilidad bruta = venta − kilos × costo; utilidad real = bruta − kilos ×
  gasto operativo vigente en la fecha de la venta (GastoOperativo, hoy $4/kg).
- Las líneas sin precio o sin costo no suman y se cuentan aparte.
"""
from bisect import bisect_right
from decimal import Decimal

from django.db import transaction
from openpyxl import Workbook
from openpyxl.styles import Font

from inventario.formatos import FORMATO_KG, FORMATO_PRECIO_KG

from .models import CambioCostoVenta, CostoVenta, GastoOperativo
from .ventas import importe, lineas_venta

CERO = Decimal('0')
CENTAVO = Decimal('0.01')

COMPLETA = 'completa'
SIN_PRECIO = 'sin_precio'
SIN_COSTO = 'sin_costo'


def _gasto_en(vigencias):
    """Función fecha → gasto por kilo vigente ese día (0 si aún no había)."""
    fechas = [g.vigente_desde for g in vigencias]

    def gasto(fecha):
        i = bisect_right(fechas, fecha)
        return vigencias[i - 1].por_kilo if i else CERO
    return gasto


def _margen(utilidad, venta):
    return (utilidad / venta * 100).quantize(Decimal('0.1')) if venta else None


def lineas(desde, hasta, cliente=None, producto=None):
    queryset = (
        lineas_venta(clientes=[cliente] if cliente else None, hasta=hasta)
        .filter(salida__fecha__gte=desde)
        .select_related('costo_cartera', 'salida__empresa', 'entrada_detalle__entrada')
    )
    if producto:
        queryset = queryset.filter(producto_id=producto)
    return queryset


def fila(linea, gasto_por_kilo):
    lote = linea.entrada_detalle
    salida = linea.salida
    if lote is None:
        referencia_lote, proveedor = 'RESGUARDO', ''
    else:
        referencia_lote = lote.lote_general.codigo if lote.lote_general_id else lote.lote_proveedor
        proveedor = lote.proveedor_origen.nombre if lote.proveedor_origen_id else ''
    camara = linea.camara.nombre if linea.camara_id else ''
    venta = importe(linea)
    try:
        capturado = linea.costo_cartera.costo_unitario
    except CostoVenta.DoesNotExist:
        capturado = None
    costo_unitario = capturado if capturado is not None else (lote.costo_por_kilo if lote else None)
    kilos = linea.total_kilos
    costo = (kilos * costo_unitario).quantize(CENTAVO) if costo_unitario is not None else None
    if venta is None:
        estado = SIN_PRECIO
    elif costo is None:
        estado = SIN_COSTO
    else:
        estado = COMPLETA
    utilidad = venta - costo if estado == COMPLETA else None
    return {
        'linea_id': linea.id,
        'fecha': salida.fecha,
        'lote': referencia_lote,
        'nota': salida.notas,
        'folio_salida': salida.folio_de_salida or '',
        'proveedor': '/'.join(filter(None, [camara, proveedor])),
        'cliente': salida.cliente.nombre,
        'producto': f'{linea.producto.talla} {linea.producto.tipo}'.strip(),
        'kilos': kilos,
        'precio': linea.precio_x_kilo if linea.precio_x_kilo is not None else (
            (venta / kilos).quantize(Decimal('0.0001')) if venta is not None and kilos else None
        ),
        'venta': venta,
        'costo_unitario': costo_unitario,
        'costo_capturado': capturado is not None,
        'costo': costo,
        'utilidad': utilidad,
        'margen': _margen(utilidad, venta) if utilidad is not None else None,
        'gasto_por_kilo': gasto_por_kilo,
        'estado': estado,
    }


def utilidad_real(desde, hasta, cliente=None, producto=None):
    gasto = _gasto_en(list(GastoOperativo.objects.order_by('vigente_desde')))
    filas = [fila(l, gasto(l.salida.fecha)) for l in lineas(desde, hasta, cliente, producto)]
    completas = [f for f in filas if f['estado'] == COMPLETA]
    venta = sum((f['venta'] for f in completas), CERO)
    utilidad = sum((f['utilidad'] for f in completas), CERO)
    gasto_operativo = sum((f['kilos'] * f['gasto_por_kilo'] for f in completas), CERO).quantize(CENTAVO)
    real = utilidad - gasto_operativo
    return {
        'filas': filas,
        'resumen': {
            'lineas': len(completas),
            'kilos': sum((f['kilos'] for f in completas), CERO),
            'venta': venta,
            'costo': sum((f['costo'] for f in completas), CERO),
            'utilidad': utilidad,
            'margen': _margen(utilidad, venta),
            'gasto_operativo': gasto_operativo,
            'utilidad_real': real,
            'margen_real': _margen(real, venta),
            'sin_precio': sum(1 for f in filas if f['estado'] == SIN_PRECIO),
            'sin_costo': sum(1 for f in filas if f['estado'] == SIN_COSTO),
            'costo_propuesto': sum(1 for f in completas if not f['costo_capturado']),
        },
    }


@transaction.atomic
def guardar_costos(costos, usuario):
    """
    `costos`: [{linea_id, costo_unitario}] ya validados (líneas de venta).
    `costo_unitario` None quita la captura (vuelve al propuesto del lote).
    Solo deja bitácora de lo que realmente cambió.
    """
    actuales = {c.salida_detalle_id: c for c in CostoVenta.objects.select_for_update().filter(
        salida_detalle_id__in=[c['linea'].id for c in costos],
    )}
    cambios = 0
    for item in costos:
        linea, nuevo = item['linea'], item['costo_unitario']
        actual = actuales.get(linea.id)
        anterior = actual.costo_unitario if actual else None
        if anterior == nuevo:
            continue
        if nuevo is None:
            actual.delete()
        elif actual:
            actual.costo_unitario, actual.usuario = nuevo, usuario
            actual.save(update_fields=['costo_unitario', 'usuario', 'modificado'])
        else:
            CostoVenta.objects.create(salida_detalle=linea, costo_unitario=nuevo, usuario=usuario)
        CambioCostoVenta.objects.create(salida_detalle=linea, anterior=anterior, nuevo=nuevo, usuario=usuario)
        cambios += 1
    return cambios


ENCABEZADOS = [
    'Fecha', 'LOTE', 'Nota', 'PROVEEDOR', 'Cliente', 'Producto', 'KILOS', 'PRECIO', 'Total Venta',
    'COSTO UNITARIO', 'IMPORTE', 'UTILIDAD', '%',
]
FORMATO_PESOS = '"$"#,##0.00;[Red]-"$"#,##0.00'


def construir_excel(datos, titulo):
    """Mismas columnas que la hoja Registro del área, con el bloque de totales al pie."""
    from inventario.reportes import _estilizar_encabezado

    def numero(v):
        return float(v) if v is not None else None

    wb = Workbook()
    ws = wb.active
    ws.title = 'Registro'
    ws.append(ENCABEZADOS)
    _estilizar_encabezado(ws, len(ENCABEZADOS))
    for f in datos['filas']:
        ws.append([
            f['fecha'], f['lote'], f['nota'], f['proveedor'], f['cliente'], f['producto'], numero(f['kilos']),
            numero(f['precio']), numero(f['venta']), numero(f['costo_unitario']), numero(f['costo']),
            numero(f['utilidad']), float(f['margen']) / 100 if f['margen'] is not None else None,
        ])
        r = ws.max_row
        ws.cell(r, 1).number_format = 'DD/MM/YYYY'
        ws.cell(r, 7).number_format = FORMATO_KG
        for c in (8, 10):
            ws.cell(r, c).number_format = FORMATO_PRECIO_KG
        for c in (9, 11, 12):
            ws.cell(r, c).number_format = FORMATO_PESOS
        ws.cell(r, 13).number_format = '0.0%'
    s = datos['resumen']
    ws.append([])
    for etiqueta, valor, formato in (
        ('TOTALES (kilos)', s['kilos'], FORMATO_KG),
        ('Venta', s['venta'], FORMATO_PESOS),
        ('Costo', s['costo'], FORMATO_PESOS),
        ('Utilidad bruta', s['utilidad'], FORMATO_PESOS),
        ('Gasto operativo', s['gasto_operativo'], FORMATO_PESOS),
        ('Utilidad real', s['utilidad_real'], FORMATO_PESOS),
    ):
        ws.append([None] * 5 + [etiqueta, numero(valor)])
        ws.cell(ws.max_row, 6).font = Font(bold=True)
        ws.cell(ws.max_row, 7).font = Font(bold=True)
        ws.cell(ws.max_row, 7).number_format = formato
    if s['sin_precio'] or s['sin_costo']:
        ws.append([f"No incluidas: {s['sin_precio']} línea(s) sin precio y {s['sin_costo']} sin costo."])
    ws.append([titulo])
    for col, ancho in zip('ABCDEFGHIJKLM', (12, 14, 14, 26, 28, 22, 12, 11, 15, 12, 15, 14, 8)):
        ws.column_dimensions[col].width = ancho
    ws.freeze_panes = 'A2'
    return wb
