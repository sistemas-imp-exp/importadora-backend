"""
Umbrales y clasificación de caducidad.

Vive aparte de alertas.py y de reportes.py porque ambos lo necesitan y
alertas.py ya importa de reportes.py: tenerlo en cualquiera de los dos
provocaba un import circular. Es matemática de fechas pura, sin querysets.

El frontend duplica estos mismos umbrales en utils/caducidad.ts — si se
ajustan aquí, ajustar también allá.
"""
from datetime import timedelta

DIAS_POR_VENCER = 30
DIAS_URGENTE = 15
DIAS_CRITICO = 7

NIVELES = ["vencido", "critico", "urgente", "por_vencer"]

# Valor que manda el chip de la pantalla de Existencias para "los lotes que
# no tienen fecha de caducidad capturada". No es un nivel: es su ausencia.
SIN_CADUCIDAD = "sin_caducidad"


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


def rango_de_nivel(nivel, hoy):
    """
    Traduce un nivel al rango de fecha_caducidad que le corresponde, para
    poder filtrar en SQL en vez de clasificar lote por lote en Python.

    Devuelve (desde, hasta) —ambos inclusive, cualquiera puede ser None para
    indicar un extremo abierto— o None si el nivel no se reconoce. Los cortes
    son los complementarios exactos de clasificar_nivel().
    """
    rangos = {
        "vencido": (None, hoy - timedelta(days=1)),
        "critico": (hoy, hoy + timedelta(days=DIAS_CRITICO)),
        "urgente": (hoy + timedelta(days=DIAS_CRITICO + 1), hoy + timedelta(days=DIAS_URGENTE)),
        "por_vencer": (hoy + timedelta(days=DIAS_URGENTE + 1), hoy + timedelta(days=DIAS_POR_VENCER)),
    }
    return rangos.get(nivel)
