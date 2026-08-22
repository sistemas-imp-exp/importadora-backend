from rest_framework import viewsets

from .models import (
    Camara,
    Cliente,
    Empresa,
    Entrada,
    EntradaDetalle,
    LoteGeneral,
    MovimientoCamara,
    Producto,
    Proveedor,
    Salida,
    SalidaDetalle,
)
from .api_serializers import (
    CamaraSerializer,
    ClienteSerializer,
    EmpresaSerializer,
    EntradaDetalleSerializer,
    EntradaSerializer,
    LoteGeneralSerializer,
    MovimientoCamaraSerializer,
    ProductoSerializer,
    ProveedorSerializer,
    SalidaDetalleSerializer,
    SalidaSerializer,
)


class EmpresaViewSet(viewsets.ModelViewSet):
    queryset = Empresa.objects.all()
    serializer_class = EmpresaSerializer


class CamaraViewSet(viewsets.ModelViewSet):
    queryset = Camara.objects.all()
    serializer_class = CamaraSerializer


class ProveedorViewSet(viewsets.ModelViewSet):
    queryset = Proveedor.objects.all()
    serializer_class = ProveedorSerializer


class ClienteViewSet(viewsets.ModelViewSet):
    queryset = Cliente.objects.all()
    serializer_class = ClienteSerializer


class ProductoViewSet(viewsets.ModelViewSet):
    queryset = Producto.objects.all()
    serializer_class = ProductoSerializer


class EntradaViewSet(viewsets.ModelViewSet):
    queryset = Entrada.objects.select_related('proveedor').prefetch_related('detalles__producto')
    serializer_class = EntradaSerializer


class LoteGeneralViewSet(viewsets.ModelViewSet):
    queryset = LoteGeneral.objects.all()
    serializer_class = LoteGeneralSerializer


class EntradaDetalleViewSet(viewsets.ModelViewSet):
    queryset = EntradaDetalle.objects.all()
    serializer_class = EntradaDetalleSerializer


class SalidaViewSet(viewsets.ModelViewSet):
    queryset = Salida.objects.select_related('cliente').prefetch_related('detalles__producto')
    serializer_class = SalidaSerializer


class SalidaDetalleViewSet(viewsets.ModelViewSet):
    queryset = SalidaDetalle.objects.all()
    serializer_class = SalidaDetalleSerializer


class MovimientoCamaraViewSet(viewsets.ModelViewSet):
    queryset = MovimientoCamara.objects.all()
    serializer_class = MovimientoCamaraSerializer
