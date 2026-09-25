from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

AREA_TESORERIA = "TES"
AREA_INVENTARIO = "INV"
AREA_ADMIN = "ADMIN"


def tiene_area(user, codigo):
    """
    Misma regla que `hasArea` del frontend (AuthProvider): el superusuario pasa
    siempre; los demás necesitan el área asignada y activa.
    """
    if not (user and user.is_authenticated):
        return False
    if user.is_superuser:
        return True
    return user.areas.filter(area__codigo=codigo, area__activo=True).exists()


class TieneArea(BasePermission):
    """Base para las permission classes por área; las subclases fijan `area`."""

    area = None
    message = "No tienes acceso a esta área."

    def has_permission(self, request, view):
        return tiene_area(request.user, self.area)


class AreaTesoreria(TieneArea):
    area = AREA_TESORERIA


class AreaInventario(TieneArea):
    area = AREA_INVENTARIO


def area_requerida(codigo):
    """
    Equivalente de TieneArea para las vistas server-rendered (app `core`):
    sin sesión redirige al login, con sesión pero sin el área responde 403.
    """

    def decorador(vista):
        @wraps(vista)
        @login_required
        def envoltura(request, *args, **kwargs):
            if not tiene_area(request.user, codigo):
                raise PermissionDenied
            return vista(request, *args, **kwargs)

        return envoltura

    return decorador
