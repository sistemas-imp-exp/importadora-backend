from rest_framework import exceptions
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import Perfil
from .sesiones import CLAIM_VERSION

# Lo único que puede hacer quien debe cambiar su contraseña.
RUTAS_SIN_PASSWORD_VIGENTE = {'me', 'me_password', 'logout'}


class CambioPasswordRequerido(exceptions.PermissionDenied):
    """403 con `code` en el cuerpo para que el frontend lleve a cambiar la contraseña."""

    def __init__(self):
        super().__init__({
            'detail': 'Debes cambiar tu contraseña antes de continuar.',
            'code': 'cambio_password_requerido',
        })


class JWTSesionAuthentication(JWTAuthentication):
    """
    JWT de SimpleJWT más dos reglas de sesión:
    - el token debe traer la versión de sesión vigente del usuario (si se
      cambió la contraseña o se cerraron sus sesiones, el token ya no sirve);
    - quien tiene contraseña provisional solo puede cambiarla.
    """

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        estado = Perfil.objects.filter(usuario_id=user.pk).values_list(
            'version_sesion', 'debe_cambiar_password',
        ).first() or (0, False)
        version, debe_cambiar = estado
        if validated_token.get(CLAIM_VERSION, 0) != version:
            raise exceptions.AuthenticationFailed('La sesión fue cerrada.', code='sesion_revocada')
        self._debe_cambiar = debe_cambiar
        return user

    def authenticate(self, request):
        self._debe_cambiar = False
        resultado = super().authenticate(request)
        if resultado is not None and self._debe_cambiar:
            match = getattr(request._request, 'resolver_match', None)
            if not match or match.url_name not in RUTAS_SIN_PASSWORD_VIGENTE:
                raise CambioPasswordRequerido()
        return resultado
