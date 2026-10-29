from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from security.models import Area


class CanalArea(models.Model):
    """
    Par de áreas que pueden comunicarse (en ambos sentidos). Solo entre áreas
    con canal activo hay avisos y mensajes; hoy Cartera ↔ Inventario.
    """
    area_a = models.ForeignKey(Area, on_delete=models.CASCADE, related_name='+')
    area_b = models.ForeignKey(Area, on_delete=models.CASCADE, related_name='+')
    activo = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['area_a', 'area_b'], name='canal_area_unico')]
        verbose_name = 'Canal entre áreas'
        verbose_name_plural = 'Canales entre áreas'

    def __str__(self):
        return f"{self.area_a.codigo} ↔ {self.area_b.codigo}"

    def clean(self):
        if self.area_a_id and self.area_a_id == self.area_b_id:
            raise ValidationError('Un canal une dos áreas distintas.')

    @classmethod
    def permitido(cls, origen, destino):
        return cls.objects.filter(activo=True).filter(
            Q(area_a=origen, area_b=destino) | Q(area_a=destino, area_b=origen)
        ).exists()


class Notificacion(models.Model):
    """
    Aviso automático o mensaje de un área a otra. Las respuestas cuelgan de la
    primera (`hilo`) y van siempre hacia el área contraria de quien responde.
    """
    AVISO = 'aviso'
    MENSAJE = 'mensaje'
    TIPO_CHOICES = [(AVISO, 'Aviso automático'), (MENSAJE, 'Mensaje')]

    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    # Qué lo generó (avisos): p. ej. "venta_diferencia", "salida_corregida".
    evento = models.CharField(max_length=40, blank=True)
    area_origen = models.ForeignKey(Area, on_delete=models.PROTECT, related_name='+')
    area_destino = models.ForeignKey(Area, on_delete=models.PROTECT, related_name='+')
    titulo = models.CharField(max_length=150)
    mensaje = models.TextField(blank=True)
    # Ruta del frontend del registro relacionado (p. ej. /inventario/salidas?busqueda=SI9510).
    enlace = models.CharField(max_length=200, blank=True)
    hilo = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='respuestas')
    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
    )
    creado = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-creado']
        verbose_name = 'Notificación'
        verbose_name_plural = 'Notificaciones'

    def __str__(self):
        return f"{self.area_origen.codigo}→{self.area_destino.codigo}: {self.titulo}"

    @property
    def raiz_id(self):
        return self.hilo_id or self.pk


class LecturaNotificacion(models.Model):
    """Leída por usuario: cada quien lleva su propio leído/no leído."""
    notificacion = models.ForeignKey(Notificacion, on_delete=models.CASCADE, related_name='lecturas')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    leida_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['notificacion', 'usuario'], name='lectura_unica')]
