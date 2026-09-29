"""Utilidades de tests compartidas entre apps."""
from django.contrib.auth import get_user_model

from .models import Area, UsuarioArea


def crear_usuario_con_area(username, *codigos, password="x", solo_lectura=False, **extra):
    """
    Crea un usuario normal con las áreas indicadas (se crean si no existen).
    `solo_lectura=True` asigna todas esas áreas en modo consulta/descarga.
    """
    usuario = get_user_model().objects.create_user(username=username, password=password, **extra)
    for codigo in codigos:
        area, _ = Area.objects.get_or_create(codigo=codigo, defaults={"nombre": codigo})
        UsuarioArea.objects.create(usuario=usuario, area=area, solo_lectura=solo_lectura)
    return usuario
