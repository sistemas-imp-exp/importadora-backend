from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from inventario.paginacion import PaginacionInventario

from .models import EventoAcceso, Perfil
from .permissions import EsSuperusuario
from .serializers import (
    ActualizarPerfilSerializer,
    CambiarPasswordSerializer,
    EventoAccesoSerializer,
    FotoPerfilSerializer,
    LoginSerializer,
    UsuarioCatalogoSerializer,
    UsuarioMeSerializer,
)
from .sesiones import (
    CLAIM_VERSION,
    anular_refresh,
    borrar_cookie,
    emitir_tokens,
    ip_cliente,
    minutos_de_bloqueo,
    poner_cookie,
    registrar,
    revocar_sesiones,
    version_de,
)

User = get_user_model()

# Dos pestañas pueden renovar casi al mismo tiempo con la misma cookie: el
# segundo uso de un refresh recién rotado se acepta dentro de este margen.
# Pasado el margen se toma como token robado y se cierran todas las sesiones.
MARGEN_REUSO = timedelta(seconds=30)


def _respuesta_sesion(request, user, refresh, codigo=status.HTTP_200_OK):
    response = Response(
        {
            "access": str(refresh.access_token),
            "user": UsuarioMeSerializer(user, context={"request": request}).data,
        },
        status=codigo,
    )
    poner_cookie(response, refresh)
    return response


def _sin_sesion(detalle, codigo='sin_sesion'):
    response = Response({"detail": detalle, "code": codigo}, status=status.HTTP_401_UNAUTHORIZED)
    borrar_cookie(response)
    return response


class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        username = serializer.validated_data["username"].strip()
        password = serializer.validated_data["password"]
        ip = ip_cliente(request)

        minutos = minutos_de_bloqueo(username, ip)
        if minutos:
            registrar(request, EventoAcceso.BLOQUEADO, username=username, detalle='Intento durante el bloqueo')
            return Response(
                {"detail": f"Demasiados intentos fallidos. Intenta de nuevo en {minutos} min."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        # authenticate() ya rechaza cuentas inactivas: mismo mensaje que una
        # contraseña incorrecta, para no revelar qué cuentas existen.
        user = authenticate(request, username=username, password=password)
        if user is None:
            existente = User.objects.filter(username__iexact=username).first()
            registrar(request, EventoAcceso.FALLIDO, usuario=existente, username=username)
            if minutos_de_bloqueo(username, ip):
                registrar(request, EventoAcceso.BLOQUEADO, usuario=existente, username=username)
            return Response({"detail": "Usuario o contraseña incorrectos."}, status=status.HTTP_400_BAD_REQUEST)

        registrar(request, EventoAcceso.INGRESO, usuario=user)
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        return _respuesta_sesion(request, user, emitir_tokens(user))


class RefreshView(APIView):
    """Renueva el access token con la cookie del refresh (y la rota)."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        crudo = request.COOKIES.get(settings.AUTH_COOKIE_NAME)
        if not crudo:
            return _sin_sesion("No hay sesión activa.")
        try:
            # Firma y vencimiento; la lista negra se revisa aparte por el margen de reuso.
            refresh = RefreshToken(crudo, verify=False)
            refresh.token_backend.decode(crudo, verify=True)
            refresh.check_exp()
            if refresh.get('token_type') != 'refresh':
                raise TokenError('Tipo de token inválido.')
        except Exception:  # noqa: BLE001 — firma inválida, vencido o mal formado
            return _sin_sesion("La sesión expiró.")

        user = User.objects.filter(pk=refresh.get('user_id'), is_active=True).first()
        if user is None or refresh.get(CLAIM_VERSION, 0) != version_de(user.pk):
            return _sin_sesion("La sesión fue cerrada.", 'sesion_revocada')

        anulado = BlacklistedToken.objects.filter(token__jti=refresh['jti']).values_list(
            'blacklisted_at', flat=True,
        ).first()
        if anulado is not None:
            if timezone.now() - anulado > MARGEN_REUSO:
                revocar_sesiones(user)
                registrar(request, EventoAcceso.REUSO_TOKEN, usuario=user,
                          detalle='Se reutilizó un token ya renovado: se cerraron todas sus sesiones')
                return _sin_sesion("La sesión fue cerrada por seguridad.", 'sesion_revocada')
        else:
            anular_refresh(refresh)

        return _respuesta_sesion(request, user, emitir_tokens(user))


class LogoutView(APIView):
    """Anula el refresh de la cookie y la borra. Funciona aunque el access ya haya vencido."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        crudo = request.COOKIES.get(settings.AUTH_COOKIE_NAME)
        user = None
        if crudo:
            try:
                refresh = RefreshToken(crudo)
                user = User.objects.filter(pk=refresh.get('user_id')).first()
                anular_refresh(refresh)
            except TokenError:
                pass
        if user is not None:
            inactividad = request.data.get('motivo') == 'inactividad'
            registrar(request, EventoAcceso.INACTIVIDAD if inactividad else EventoAcceso.SALIDA, usuario=user)
        response = Response(status=status.HTTP_204_NO_CONTENT)
        borrar_cookie(response)
        return response


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UsuarioMeSerializer(request.user, context={"request": request}).data)

    def patch(self, request):
        serializer = ActualizarPerfilSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(UsuarioMeSerializer(request.user, context={"request": request}).data)


class CambiarPasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = CambiarPasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # Cierra las sesiones en otros equipos y deja abierta esta con tokens nuevos.
        revocar_sesiones(user)
        Perfil.objects.filter(usuario=user).update(debe_cambiar_password=False)
        registrar(request, EventoAcceso.PASSWORD, usuario=user)
        response = _respuesta_sesion(request, user, emitir_tokens(user))
        response.data["detail"] = "Contraseña actualizada correctamente."
        return response


class FotoPerfilView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        perfil, _ = Perfil.objects.get_or_create(usuario=request.user)

        serializer = FotoPerfilSerializer(perfil, data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(serializer.data)

    def delete(self, request):
        perfil, _ = Perfil.objects.get_or_create(usuario=request.user)

        if perfil.foto:
            perfil.foto.delete(save=False)
            perfil.foto = None
            perfil.save()

        return Response(status=status.HTTP_204_NO_CONTENT)


class UsuarioViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    # Sin destroy: un usuario se desactiva (is_active=False), no se elimina.
    queryset = User.objects.all().order_by('username')
    serializer_class = UsuarioCatalogoSerializer
    permission_classes = [IsAuthenticated, EsSuperusuario]

    @action(detail=True, methods=['post'], url_path='cerrar-sesiones')
    def cerrar_sesiones(self, request, pk=None):
        """Saca al usuario de todos los equipos donde tenga sesión abierta."""
        usuario = self.get_object()
        revocar_sesiones(usuario)
        registrar(request, EventoAcceso.SESIONES_CERRADAS, usuario=usuario,
                  detalle=f'Por {request.user.username}')
        return Response({"detail": f"Se cerraron las sesiones de {usuario.username}."})

    @action(detail=True, methods=['post'])
    def desbloquear(self, request, pk=None):
        """Quita el bloqueo por intentos fallidos sin esperar los 15 minutos."""
        usuario = self.get_object()
        registrar(request, EventoAcceso.DESBLOQUEO, usuario=usuario, detalle=f'Por {request.user.username}')
        return Response(self.get_serializer(usuario).data)


class BitacoraAccesosView(generics.ListAPIView):
    """Bitácora de accesos (solo superusuario), lo más reciente primero."""
    serializer_class = EventoAccesoSerializer
    permission_classes = [IsAuthenticated, EsSuperusuario]
    pagination_class = PaginacionInventario

    def get_queryset(self):
        queryset = EventoAcceso.objects.select_related('usuario')
        params = self.request.query_params
        if params.get('usuario'):
            queryset = queryset.filter(usuario_id=params['usuario'])
        if params.get('evento'):
            queryset = queryset.filter(evento=params['evento'])
        if params.get('busqueda'):
            queryset = queryset.filter(username__icontains=params['busqueda'].strip())
        desde = parse_date(params.get('desde') or '')
        hasta = parse_date(params.get('hasta') or '')
        if desde:
            queryset = queryset.filter(fecha__date__gte=desde)
        if hasta:
            queryset = queryset.filter(fecha__date__lte=hasta)
        return queryset
