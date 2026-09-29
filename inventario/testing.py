"""Utilidades de tests de inventario."""
from .models import Empresa


def empresa_importadora():
    """La empresa por defecto; la crea la migración 0019, así que ya existe en la base de pruebas."""
    return Empresa.objects.get(nombre="IMPORTADORA")
