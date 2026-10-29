from django.urls import include, path
from rest_framework.routers import DefaultRouter
from .views import (
    BitacoraAccesosView,
    CambiarPasswordView,
    FotoPerfilView,
    LoginView,
    LogoutView,
    MeView,
    RefreshView,
    UsuarioViewSet,
)

router = DefaultRouter()
router.register(r'usuarios', UsuarioViewSet)

# Sin registro público: las cuentas las crea un superusuario desde el módulo de
# Usuarios (UsuarioViewSet), que además les asigna sus áreas.
urlpatterns = [
    path("login/", LoginView.as_view(), name="login"),
    # El refresh viaja en cookie httpOnly (ver accounts/sesiones.py).
    path("refresh/", RefreshView.as_view(), name="token_refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", MeView.as_view(), name="me"),
    path("me/password/", CambiarPasswordView.as_view(), name="me_password"),
    path("bitacora/", BitacoraAccesosView.as_view(), name="bitacora_accesos"),
    path("me/foto/", FotoPerfilView.as_view()),
    path("", include(router.urls)),
]
