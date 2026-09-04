from datetime import timedelta

from django.utils import timezone

# Se re-exportan para no romper a quien ya los importaba desde aquí.
from .caducidad import (  # noqa: F401
    DIAS_CRITICO,
    DIAS_POR_VENCER,
    DIAS_URGENTE,
    NIVELES,
    clasificar_nivel,
)
from .reportes import obtener_lotes_filtrados


def obtener_lotes_por_vencer(params):
    """
    params: QueryDict (request.query_params). Devuelve una lista de dicts
    (no un queryset) con cada lote en ventana de alerta —ya tiene existencia
    disponible, tiene fecha_caducidad capturada, y esa fecha cae dentro de
    los próximos DIAS_POR_VENCER días (o ya venció)— junto con sus días
    restantes y nivel, ordenados del más urgente al menos urgente.
    """
    hoy = timezone.localdate()
    limite = hoy + timedelta(days=DIAS_POR_VENCER)

    lotes = (
        obtener_lotes_filtrados(params)
        .filter(fecha_caducidad__isnull=False, fecha_caducidad__lte=limite)
    )

    filtro_nivel = params.get('nivel')

    resultado = []
    for lote in lotes:
        dias_restantes = (lote.fecha_caducidad - hoy).days
        nivel = clasificar_nivel(dias_restantes)
        if nivel is None:
            continue
        if filtro_nivel and nivel != filtro_nivel:
            continue
        resultado.append({
            'lote': lote,
            'dias_restantes': dias_restantes,
            'nivel': nivel,
        })

    orden_nivel = {n: i for i, n in enumerate(NIVELES)}
    resultado.sort(key=lambda item: (orden_nivel[item['nivel']], item['dias_restantes']))
    return resultado
