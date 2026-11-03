from django.urls import path

from .resguardos_views import (
    ClientesConResguardoView,
    EntregasCapturadasView,
    LigarEntregasView,
    LotesParaRetiroView,
    ResguardosView,
    ResumenResguardosView,
    RetiroDetalleView,
    RetirosView,
)

urlpatterns = [
    path('', ResguardosView.as_view()),
    path('clientes/', ClientesConResguardoView.as_view()),
    path('resumen/', ResumenResguardosView.as_view()),
    path('resumen/excel/', ResumenResguardosView.as_view(), {'formato': 'excel'}),
    path('resumen/pdf/', ResumenResguardosView.as_view(), {'formato': 'pdf'}),
    path('lotes/', LotesParaRetiroView.as_view()),
    path('entregas-capturadas/', EntregasCapturadasView.as_view()),
    path('ligar/', LigarEntregasView.as_view()),
    path('retiros/', RetirosView.as_view()),
    path('retiros/<int:pk>/', RetiroDetalleView.as_view()),
]
