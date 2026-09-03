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
from .alertas_views import listar_alertas_caducidad
from .auditoria_views import editar_entrada_auditada, listar_ediciones_entrada
from .existencias_views import listar_existencias
from .reportes_views import exportar_existencias_excel, exportar_existencias_pdf

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
    path('reportes/existencias/excel/', exportar_existencias_excel),
    path('reportes/existencias/pdf/', exportar_existencias_pdf),
    path('existencias/', listar_existencias),
    path('alertas/caducidad/', listar_alertas_caducidad),
    path('auditoria/entradas/', listar_ediciones_entrada),
    path('auditoria/entradas/<int:entrada_id>/', editar_entrada_auditada),
    path('', include(router.urls)),
]
