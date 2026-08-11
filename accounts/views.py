from django.contrib.auth import get_user_model
from rest_framework import mixins, status, viewsets
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Perfil
from .permissions import EsSuperusuario
from .serializers import (
    ActualizarPerfilSerializer,
    CambiarPasswordSerializer,
    FotoPerfilSerializer,
    LoginSerializer,
    RegistroSerializer,
    UsuarioCatalogoSerializer,
    UsuarioMeSerializer,
)

User = get_user_model()


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UsuarioMeSerializer(user, context={"request": request}).data,
            },
            status=status.HTTP_200_OK,
        )


class RegistroView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegistroSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.save()

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UsuarioMeSerializer(user, context={"request": request}).data,
            },
            status=status.HTTP_201_CREATED,
        )


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
        serializer.save()

        return Response({"detail": "Contraseña actualizada correctamente."})


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
