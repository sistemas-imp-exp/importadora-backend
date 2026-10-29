"""
Ventas de Cartera: las líneas de salida de Inventario a un cliente,
posteriores al corte de la carga inicial (lo anterior ya está en el saldo
inicial). Cada línea suma a la empresa de su lote por su importe: el total de
venta capturado o, si falta, kilos × precio; sin precio no suma y se avisa.

Cartera las verifica línea por línea contra el papel (VerificacionVenta). Si
Inventario corrige la línea después, la huella deja de coincidir y la línea
vuelve a estar pendiente.
"""
from decimal import Decimal

from inventario.models import SalidaDetalle

from .models import VerificacionVenta
from .saldos import fecha_corte

PENDIENTE = 'pendiente'
SIN_PRECIO = 'sin_precio'
CENTAVO = Decimal('0.01')


def lineas_venta(clientes=None, hasta=None):
    # Los retiros de resguardo entregan producto ya vendido (y cobrado): no son venta.
    queryset = (
        SalidaDetalle.objects.filter(salida__cliente__isnull=False, retiro_de__isnull=True)
        .select_related(
            'salida__cliente', 'producto', 'camara', 'entrada_detalle__lote_general', 'entrada_detalle__proveedor_origen',
            'entrada_detalle__entrada__empresa', 'verificacion_cartera__usuario',
        )
        .order_by('salida__fecha', 'salida__folio_de_salida', 'id')
    )
    corte = fecha_corte()
    if corte:
        queryset = queryset.filter(salida__fecha__gt=corte)
    if clientes is not None:
        queryset = queryset.filter(salida__cliente_id__in=clientes)
    if hasta is not None:
        queryset = queryset.filter(salida__fecha__lte=hasta)
    return queryset


def importe(linea):
    """Importe de la línea, o None si no tiene precio."""
    if linea.total_venta is not None:
        return linea.total_venta
    if linea.precio_x_kilo is not None:
        return (linea.total_kilos * linea.precio_x_kilo).quantize(CENTAVO)
    return None


def huella(linea):
    """
    Lo que Cartera revisa de la línea: si cambia, la revisión deja de valer.
    Los decimales van sin ceros a la derecha: cambiar la precisión de la
    columna no es un cambio de la línea.
    """
    return '|'.join(format(v.normalize(), 'f') if isinstance(v, Decimal) else str(v) for v in (
        linea.salida.cliente_id, linea.producto_id, linea.entrada_detalle_id,
        linea.total_kilos, linea.precio_x_kilo, linea.total_venta,
    ))


def verificacion_vigente(linea):
    """La revisión de la línea si sigue valiendo (Inventario no la corrigió después)."""
    try:
        verificacion = linea.verificacion_cartera
    except VerificacionVenta.DoesNotExist:
        return None
    return verificacion if verificacion.huella == huella(linea) else None


def estado(linea):
    """
    (estado, cambio): 'sin_precio' | 'pendiente' | 'verificada' | 'diferencia'.
    `cambio` = había revisión pero Inventario corrigió la línea después.
    """
    try:
        revisada = linea.verificacion_cartera is not None
    except VerificacionVenta.DoesNotExist:
        revisada = False
    vigente = verificacion_vigente(linea)
    if importe(linea) is None:
        return SIN_PRECIO, False
    if vigente is None:
        return PENDIENTE, revisada
    return vigente.estado, False


def empresa_de(linea):
    return linea.entrada_detalle.entrada.empresa


def producto_de(linea):
    return f'{linea.producto.talla} {linea.producto.tipo}'.strip()
