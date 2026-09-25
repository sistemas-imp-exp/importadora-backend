from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from security.permissions import AreaInventario

from .alertas import obtener_lotes_por_vencer


def _serializar(item):
    lote = item['lote']
    return {
        'id': lote.id,
        'camara': lote.camara.nombre if lote.camara_id else None,
        'proveedor': lote.proveedor_origen.nombre if lote.proveedor_origen_id else None,
        'talla': lote.producto.talla,
        'tipo': lote.producto.tipo,
        'lote_proveedor': lote.lote_proveedor,
        'cajas_disponibles': lote.cajas_disp,
        'kilos_disponibles': str(lote.kilos_disp),
        'fecha_caducidad': lote.fecha_caducidad.isoformat(),
        'dias_restantes': item['dias_restantes'],
        'nivel': item['nivel'],
    }


@api_view(['GET'])
@permission_classes([AreaInventario])
def listar_alertas_caducidad(request):
    alertas = obtener_lotes_por_vencer(request.query_params)

    conteo_por_nivel = {'vencido': 0, 'critico': 0, 'urgente': 0, 'por_vencer': 0}
    for item in alertas:
        conteo_por_nivel[item['nivel']] += 1

    return Response({
        'total': len(alertas),
        'conteo_por_nivel': conteo_por_nivel,
        'alertas': [_serializar(item) for item in alertas],
    })
