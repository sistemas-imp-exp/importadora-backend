from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from accounts.permissions import EsSuperusuario

from .auditoria import (
    CAMPOS_DETALLE,
    CAMPOS_ENTRADA,
    ETIQUETAS_BITACORA,
    EdicionInvalida,
    aplicar_edicion,
    entrada_tiene_salidas,
)
from .models import EdicionEntrada, Entrada

# Cubre tanto los campos de la vía auditada como los de las ediciones normales
# del módulo de Inventario, que también se registran en la bitácora.
ETIQUETAS = {**ETIQUETAS_BITACORA, **CAMPOS_ENTRADA, **CAMPOS_DETALLE}


def _serializar_registro(registro):
    # Se leen las referencias denormalizadas y no las FK: la entrada o la línea
    # pueden haber sido borradas después (las FK quedan en NULL por SET_NULL).
    return {
        'id': registro.id,
        'entrada_id': registro.entrada_id,
        'entrada_referencia': registro.entrada_referencia,
        'entrada_existe': registro.entrada_id is not None,
        'linea_id': registro.entrada_detalle_id,
        'linea_referencia': registro.linea_referencia,
        'campo': registro.campo,
        'campo_etiqueta': ETIQUETAS.get(registro.campo, registro.campo),
        'valor_anterior': registro.valor_anterior,
        'valor_nuevo': registro.valor_nuevo,
        'motivo': registro.motivo,
        'editado_por': registro.editado_por.username if registro.editado_por_id else None,
        'editado_en': registro.editado_en.isoformat(),
    }


@api_view(['GET'])
@permission_classes([EsSuperusuario])
def listar_ediciones_entrada(request):
    """Bitácora de ediciones. Filtros opcionales: ?entrada=<id>&usuario=<username>."""
    registros = EdicionEntrada.objects.select_related('editado_por')

    entrada_id = request.query_params.get('entrada')
    if entrada_id:
        registros = registros.filter(entrada_id=entrada_id)

    usuario = request.query_params.get('usuario')
    if usuario:
        registros = registros.filter(editado_por__username=usuario)

    return Response({
        'campos_editables': {
            'cabecera': CAMPOS_ENTRADA,
            'lineas': CAMPOS_DETALLE,
        },
        'registros': [_serializar_registro(r) for r in registros[:500]],
    })


@api_view(['PATCH'])
@permission_classes([EsSuperusuario])
def editar_entrada_auditada(request, entrada_id):
    """
    Edita los campos que no alteran la contabilidad del inventario, incluso en
    entradas que ya tienen salidas, dejando rastro en la bitácora.
    """
    entrada = get_object_or_404(Entrada, id=entrada_id)

    try:
        registros = aplicar_edicion(entrada, request.data, request.user)
    except EdicionInvalida as e:
        return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    return Response({
        'entrada_id': entrada.id,
        'tiene_salidas': entrada_tiene_salidas(entrada),
        'cambios': [_serializar_registro(r) for r in registros],
    })
