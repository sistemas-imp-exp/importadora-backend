from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api_views import (
    AnimalViewSet, CategoriaViewSet, EspecieViewSet, InventarioView, MovimientoViewSet, RanchoViewSet,
    ReporteMovimientosView,
)

router = DefaultRouter()
router.register(r'ranchos', RanchoViewSet, basename='ganado-ranchos')
router.register(r'especies', EspecieViewSet, basename='ganado-especies')
router.register(r'categorias', CategoriaViewSet, basename='ganado-categorias')
router.register(r'animales', AnimalViewSet, basename='ganado-animales')
router.register(r'movimientos', MovimientoViewSet, basename='ganado-movimientos')

urlpatterns = [
    path('reportes/inventario/', InventarioView.as_view()),
    path('reportes/inventario/pdf/', InventarioView.as_view(formato='pdf')),
    path('reportes/inventario/excel/', InventarioView.as_view(formato='excel')),
    path('reportes/movimientos/', ReporteMovimientosView.as_view()),
    path('reportes/movimientos/pdf/', ReporteMovimientosView.as_view(formato='pdf')),
    path('reportes/movimientos/excel/', ReporteMovimientosView.as_view(formato='excel')),
    path('', include(router.urls)),
]
