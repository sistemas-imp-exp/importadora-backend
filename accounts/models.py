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

    def __str__(self):
        return f"Perfil de {self.usuario.username}"
