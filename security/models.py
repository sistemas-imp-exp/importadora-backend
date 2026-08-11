from django.conf import settings
from django.db import models


class Area(models.Model):
    nombre = models.CharField(max_length=100)
    codigo = models.CharField(max_length=20, unique=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "Área"
        verbose_name_plural = "Áreas"

    def __str__(self):
        return self.nombre


class UsuarioArea(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="areas"
    )

    area = models.ForeignKey(
        Area,
        on_delete=models.CASCADE,
        related_name="usuarios"
    )

    class Meta:
        unique_together = ("usuario", "area")
        verbose_name = "Área de usuario"
        verbose_name_plural = "Áreas de usuarios"

    def __str__(self):
        return f"{self.usuario.username} - {self.area.codigo}"