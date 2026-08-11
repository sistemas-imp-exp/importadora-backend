from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    CambiarPasswordView,
    FotoPerfilView,
    LoginView,
    MeView,
    RegistroView,
    UsuarioViewSet,
)

router = DefaultRouter()
router.register(r'usuarios', UsuarioViewSet)

urlpatterns = [
    path("login/", LoginView.as_view(), name="login"),
    path("registro/", RegistroView.as_view(), name="registro"),
    path("refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("me/", MeView.as_view()),
    path("me/password/", CambiarPasswordView.as_view()),
    path("me/foto/", FotoPerfilView.as_view()),
    path("", include(router.urls)),
]
