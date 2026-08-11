from django.urls import include, path
from rest_framework.routers import DefaultRouter
from .api_views import (
    ArqueoCajaViewSet,
    BancoViewSet,
    CorteCajaViewSet,
    DenominacionViewSet,
    DivisaViewSet,
    EmpleadoViewSet,
    MovimientoArchivoViewSet,
    MovimientoTesoreriaViewSet,
    NominaSemanalViewSet,
    PuestoViewSet,
    RanchoViewSet,
    SaldoCajaViewSet,
)
from .reportes_views import resumen_movimientos, exportar_movimientos_excel, exportar_movimientos_pdf
from .reportes_views_nomina import resumen_nomina, exportar_nomina_excel, exportar_nomina_pdf
from .reportes_views_arqueo import exportar_arqueo_excel, exportar_arqueo_pdf

router = DefaultRouter()
router.register(r'divisas', DivisaViewSet)
router.register(r'cortes', CorteCajaViewSet)
router.register(r'movimientos', MovimientoTesoreriaViewSet)
router.register(r'saldos', SaldoCajaViewSet)
router.register(r'denominaciones', DenominacionViewSet)
router.register(r'arqueos', ArqueoCajaViewSet)
router.register(r'archivos-movimiento', MovimientoArchivoViewSet)
router.register(r'ranchos', RanchoViewSet)
router.register(r'puestos', PuestoViewSet)
router.register(r'bancos', BancoViewSet)
router.register(r'empleados', EmpleadoViewSet)
router.register(r'nominas', NominaSemanalViewSet)

urlpatterns = [
    path('reportes/movimientos/excel/', exportar_movimientos_excel),
    path('reportes/movimientos/pdf/', exportar_movimientos_pdf),
    path('reportes/movimientos/', resumen_movimientos),
    path('reportes/nomina/excel/', exportar_nomina_excel),
    path('reportes/nomina/pdf/', exportar_nomina_pdf),
    path('reportes/nomina/', resumen_nomina),
    path('reportes/arqueo/<int:arqueo_id>/excel/', exportar_arqueo_excel),
    path('reportes/arqueo/<int:arqueo_id>/pdf/', exportar_arqueo_pdf),
    path('', include(router.urls)),
]
