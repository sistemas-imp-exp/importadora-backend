from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    # Sesión de Django solo para estas pantallas legadas (el login de /admin/
    # exige is_staff). Nombres con prefijo: "login" ya lo usa api/auth/.
    path('login/', auth_views.LoginView.as_view(template_name='login.html'), name='core_login'),
    path('logout/', auth_views.LogoutView.as_view(), name='core_logout'),
    path('', views.index, name='index'),
    path('apertura/', views.apertura_caja, name='apertura_caja'),
    path('movimiento/', views.registro_movimiento, name='registro_movimiento'),
    path('movimientos/', views.consulta_movimientos, name='consulta_movimientos'),
    path('cierre/', views.cierre_caja, name='cierre_caja'),
    path('divisas/', views.administrar_divisas, name='administrar_divisas'),
]
