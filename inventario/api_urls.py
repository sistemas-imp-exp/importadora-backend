from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api_views import (
    CamaraViewSet,
    ClienteViewSet,
    EmpresaViewSet,
    EntradaDetalleViewSet,
    EntradaViewSet,
    LoteGeneralViewSet,
    MovimientoCamaraViewSet,
    ProductoViewSet,
    ProveedorViewSet,
    SalidaDetalleViewSet,
    SalidaViewSet,
)

router = DefaultRouter()
router.register(r'empresas', EmpresaViewSet)
router.register(r'camaras', CamaraViewSet)
router.register(r'proveedores', ProveedorViewSet)
router.register(r'clientes', ClienteViewSet)
router.register(r'productos', ProductoViewSet)
router.register(r'entradas', EntradaViewSet)
router.register(r'lotes-generales', LoteGeneralViewSet)
router.register(r'entradas-detalle', EntradaDetalleViewSet)
router.register(r'salidas', SalidaViewSet)
router.register(r'salidas-detalle', SalidaDetalleViewSet)
router.register(r'movimientos-camara', MovimientoCamaraViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
