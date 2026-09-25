from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from security.permissions import AreaInventario

from .models import SalidaDetalle
from .reportes import obtener_lotes_filtrados


def _serializar(lote):
    documento = lote.documento_origen
    return {
        'detalle_id': lote.id,
        'fecha': lote.entrada.fecha.isoformat(),
        'producto_id': lote.producto_id,
        'precio_venta_planeado': str(lote.precio_venta_planeado) if lote.precio_venta_planeado is not None else None,
        'camara_id': lote.camara_id,
        'camara_nombre': lote.camara.nombre if lote.camara_id else 'Venta directa (sin cámara)',
        'proveedor_nombre': lote.proveedor_origen.nombre if lote.proveedor_origen_id else '—',
        'talla': lote.producto.talla,
        'tipo': lote.producto.tipo,
        'lote_proveedor': lote.lote_proveedor,
        'recibo_ingreso': documento['recibo'] or '—',
        'factura': documento['factura'] or '—',
        'peso_por_caja': str(lote.peso_por_caja),
        'cajas_disponibles': lote.cajas_disp,
        'total_kilos': str(lote.total_kilos),
        'kilos_vendidos': str(lote.kilos_vendidos),
        'kilos_disponibles': str(lote.kilos_disp),
        'costo_por_kilo': str(lote.costo_por_kilo) if lote.costo_por_kilo is not None else None,
        'fecha_caducidad': lote.fecha_caducidad.isoformat() if lote.fecha_caducidad else None,
    }


@api_view(['GET'])
@permission_classes([AreaInventario])
def listar_existencias(request):
    """
    Foto plana del inventario disponible: una fila por lote con existencia.

    Existe aparte de /entradas/ porque esa lista devuelve TODAS las entradas con
    sus lotes anidados (2 MB y varios segundos de serialización) cuando esta
    pantalla solo necesita los lotes con cajas > 0 y sin anidamiento.
    """
    # ?salida=<id> agrega los lotes que esa salida ya consumió aunque hayan
    # quedado en cero: al editarla hay que poder ver y ajustar sus propias líneas.
    ids_extra = None
    salida_id = request.query_params.get('salida')
    if salida_id:
        ids_extra = list(
            SalidaDetalle.objects
            .filter(salida_id=salida_id)
            .values_list('entrada_detalle_id', flat=True)
        )

    lotes = obtener_lotes_filtrados(request.query_params, ids_extra=ids_extra)
    return Response([_serializar(lote) for lote in lotes])
