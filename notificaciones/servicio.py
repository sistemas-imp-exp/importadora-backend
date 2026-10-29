"""
Reglas de visibilidad y envío de notificaciones entre áreas.

- Cada usuario ve lo que su área envió o recibió; el superusuario ve todo.
- No leídas = recibidas por alguna de mis áreas, que no escribí yo y que no he leído.
- Solo hay avisos y mensajes entre áreas con canal activo (CanalArea).
"""
from django.db.models import Exists, OuterRef, Q

from security.models import Area
from security.permissions import AREA_CARTERA, AREA_INVENTARIO, tiene_area

from .models import CanalArea, LecturaNotificacion, Notificacion


def areas_de(user):
    """Ids de las áreas activas del usuario (todas para el superusuario)."""
    activas = Area.objects.filter(activo=True)
    if user.is_superuser:
        return set(activas.values_list('id', flat=True))
    return set(activas.filter(usuarios__usuario=user).values_list('id', flat=True))


def visibles(user):
    queryset = Notificacion.objects.select_related('area_origen', 'area_destino', 'creada_por')
    if user.is_superuser:
        return queryset
    mias = areas_de(user)
    return queryset.filter(Q(area_origen_id__in=mias) | Q(area_destino_id__in=mias))


def con_leida(queryset, user):
    return queryset.annotate(
        leida=Exists(LecturaNotificacion.objects.filter(notificacion=OuterRef('pk'), usuario=user)),
    )


def no_leidas(user):
    queryset = Notificacion.objects.exclude(creada_por=user).exclude(lecturas__usuario=user)
    if not user.is_superuser:
        queryset = queryset.filter(area_destino_id__in=areas_de(user))
    return queryset


def marcar_leidas(user, queryset):
    pendientes = queryset.exclude(lecturas__usuario=user).values_list('pk', flat=True)
    LecturaNotificacion.objects.bulk_create(
        [LecturaNotificacion(notificacion_id=pk, usuario=user) for pk in pendientes], ignore_conflicts=True,
    )


def canales_de(user):
    """[(origen, destino)] de áreas a las que el usuario puede escribir (con escritura en el origen)."""
    pares = []
    for canal in CanalArea.objects.filter(activo=True, area_a__activo=True, area_b__activo=True).select_related(
        'area_a', 'area_b',
    ):
        for origen, destino in ((canal.area_a, canal.area_b), (canal.area_b, canal.area_a)):
            if tiene_area(user, origen.codigo, escritura=True):
                pares.append((origen, destino))
    return pares


def notificar(origen, destino, evento, titulo, mensaje='', enlace='', usuario=None):
    """Aviso automático de un área a otra; no hace nada si no hay canal activo."""
    origen = Area.objects.filter(codigo=origen).first()
    destino = Area.objects.filter(codigo=destino).first()
    if origen is None or destino is None or not CanalArea.permitido(origen, destino):
        return None
    return Notificacion.objects.create(
        tipo=Notificacion.AVISO, evento=evento, area_origen=origen, area_destino=destino,
        titulo=titulo[:150], mensaje=mensaje, enlace=enlace[:200], creada_por=usuario,
    )


def puntos(user):
    """
    Trabajo pendiente por opción del menú ({ruta: cantidad}), para los puntos
    del sidebar. Se quitan solos al atenderlo:
    - Cartera › Clientes: ventas por verificar contra la nota.
    - Inventario › Salidas: líneas con diferencia reportada por Cartera, sin corregir.
    """
    resultado = {}
    if tiene_area(user, AREA_CARTERA):
        from cartera.ventas import PENDIENTE, estado, lineas_venta
        pendientes = sum(1 for linea in lineas_venta() if estado(linea)[0] == PENDIENTE)
        if pendientes:
            resultado['/cartera/clientes'] = pendientes
    if tiene_area(user, AREA_INVENTARIO):
        from cartera.models import VerificacionVenta
        from cartera.ventas import huella
        diferencias = VerificacionVenta.objects.filter(estado=VerificacionVenta.DIFERENCIA).select_related(
            'salida_detalle__salida',
        )
        con_diferencia = sum(1 for v in diferencias if v.huella == huella(v.salida_detalle))
        if con_diferencia:
            resultado['/inventario/salidas'] = con_diferencia
    return resultado
