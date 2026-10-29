from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api_views import (
    AjusteClienteViewSet,
    ClienteCarteraViewSet,
    EmpresaCarteraViewSet,
    IngresosTesoreriaView,
    PagoClienteViewSet,
    ReporteSaldosView,
    ResumenCarteraView,
    SaldoInicialViewSet,
    VerificarVentasView,
)

router = DefaultRouter()
router.register(r'empresas', EmpresaCarteraViewSet, basename='cartera-empresas')
router.register(r'clientes', ClienteCarteraViewSet, basename='cartera-clientes')
router.register(r'pagos', PagoClienteViewSet, basename='cartera-pagos')
router.register(r'ajustes', AjusteClienteViewSet, basename='cartera-ajustes')
router.register(r'saldos-iniciales', SaldoInicialViewSet, basename='cartera-saldos-iniciales')

urlpatterns = [
    path('resumen/', ResumenCarteraView.as_view()),
    path('ingresos-tesoreria/', IngresosTesoreriaView.as_view()),
    path('ventas/verificar/', VerificarVentasView.as_view()),
    path('reportes/saldos/', ReporteSaldosView.as_view()),
    path('reportes/saldos/pdf/', ReporteSaldosView.as_view(formato='pdf')),
    path('reportes/saldos/excel/', ReporteSaldosView.as_view(formato='excel')),
    path('', include(router.urls)),
]
