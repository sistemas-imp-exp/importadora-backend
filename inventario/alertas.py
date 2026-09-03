from datetime import timedelta

from django.utils import timezone

from .reportes import obtener_lotes_filtrados

DIAS_POR_VENCER = 30
DIAS_URGENTE = 15
DIAS_CRITICO = 7

NIVELES = ["vencido", "critico", "urgente", "por_vencer"]


def clasificar_nivel(dias_restantes):
    """
    dias_restantes: (fecha_caducidad - hoy).days, puede ser negativo si ya
    venció. Devuelve None cuando está fuera de la ventana de alerta (no hay
    nada que avisar todavía).
    """
    if dias_restantes < 0:
        return "vencido"
    if dias_restantes <= DIAS_CRITICO:
        return "critico"
    if dias_restantes <= DIAS_URGENTE:
        return "urgente"
    if dias_restantes <= DIAS_POR_VENCER:
        return "por_vencer"
    return None


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
