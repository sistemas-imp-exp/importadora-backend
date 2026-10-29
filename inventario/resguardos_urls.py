from django.urls import path

from .resguardos_views import (
    ClientesConResguardoView,
    LotesParaRetiroView,
    ResguardosView,
    RetiroDetalleView,
    RetirosView,
)

urlpatterns = [
    path('', ResguardosView.as_view()),
    path('clientes/', ClientesConResguardoView.as_view()),
    path('lotes/', LotesParaRetiroView.as_view()),
    path('retiros/', RetirosView.as_view()),
    path('retiros/<int:pk>/', RetiroDetalleView.as_view()),
]
