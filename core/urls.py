from django.urls import path

from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('apertura/', views.apertura_caja, name='apertura_caja'),
    path('movimiento/', views.registro_movimiento, name='registro_movimiento'),
    path('movimientos/', views.consulta_movimientos, name='consulta_movimientos'),
    path('cierre/', views.cierre_caja, name='cierre_caja'),
    path('divisas/', views.administrar_divisas, name='administrar_divisas'),
]
