from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models


def ruta_foto_perfil(instance, filename):
    return f"usuarios/{instance.usuario_id}/{filename}"


class Perfil(models.Model):
    EXTENSIONES_PERMITIDAS = ['png', 'jpg', 'jpeg']
    TAMANO_MAXIMO_BYTES = 5 * 1024 * 1024  # 5 MB

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil",
    )
    foto = models.ImageField(
        upload_to=ruta_foto_perfil,
        null=True,
        blank=True,
        validators=[FileExtensionValidator(
            allowed_extensions=EXTENSIONES_PERMITIDAS,
            message="No se permite el tipo de imagen “%(extension)s”. Solo se aceptan: %(allowed_extensions)s.",
        )],
    )

    # Al subir de versión se invalidan todos los tokens emitidos antes (los
    # tokens llevan la versión en el claim "ver"): cambio de contraseña,
    # desactivación o "Cerrar sesiones" desde Usuarios.
    version_sesion = models.PositiveIntegerField(default=0)
    # Cuentas nuevas o con contraseña restablecida por el superusuario: deben
    # cambiarla antes de usar el sistema.
    debe_cambiar_password = models.BooleanField(default=False)

    def __str__(self):
        return f"Perfil de {self.usuario.username}"


class EventoAcceso(models.Model):
    """Bitácora de accesos: entradas, intentos fallidos, bloqueos y salidas."""

    INGRESO = 'ingreso'
    FALLIDO = 'fallido'
    BLOQUEADO = 'bloqueado'
    SALIDA = 'salida'
    INACTIVIDAD = 'inactividad'
    PASSWORD = 'password'
    SESIONES_CERRADAS = 'sesiones_cerradas'
    DESBLOQUEO = 'desbloqueo'
    REUSO_TOKEN = 'reuso_token'
    EVENTO_CHOICES = [
        (INGRESO, 'Inicio de sesión'),
        (FALLIDO, 'Intento fallido'),
        (BLOQUEADO, 'Bloqueo por intentos'),
        (SALIDA, 'Cierre de sesión'),
        (INACTIVIDAD, 'Cierre por inactividad'),
        (PASSWORD, 'Cambio de contraseña'),
        (SESIONES_CERRADAS, 'Sesiones cerradas'),
        (DESBLOQUEO, 'Desbloqueo'),
        (REUSO_TOKEN, 'Token reutilizado'),
    ]

    fecha = models.DateTimeField(auto_now_add=True, db_index=True)
    evento = models.CharField(max_length=20, choices=EVENTO_CHOICES)
    # El usuario si existe; `username` guarda lo tecleado aunque no exista
    # (los bloqueos cuentan por nombre para no revelar qué cuentas existen).
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    username = models.CharField(max_length=150, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    navegador = models.CharField(max_length=300, blank=True)
    detalle = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-fecha']
        indexes = [models.Index(fields=['username', 'fecha'])]
        verbose_name = 'Evento de acceso'
        verbose_name_plural = 'Bitácora de accesos'

    def __str__(self):
        return f"{self.get_evento_display()} · {self.username} · {self.fecha:%d/%m/%Y %H:%M}"
