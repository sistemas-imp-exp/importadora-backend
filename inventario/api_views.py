from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

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
    MovimientoCamaraCrearSerializer,
    MovimientoCamaraSerializer,
    ProductoSerializer,
    ProveedorSerializer,
    SalidaDetalleSerializer,
    SalidaSerializer,
)


class EmpresaViewSet(viewsets.ModelViewSet):
    queryset = Empresa.objects.all()
    serializer_class = EmpresaSerializer

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)


class CamaraViewSet(viewsets.ModelViewSet):
    queryset = Camara.objects.all()
    serializer_class = CamaraSerializer

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)


class ProveedorViewSet(viewsets.ModelViewSet):
    queryset = Proveedor.objects.all()
    serializer_class = ProveedorSerializer

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)


class ClienteViewSet(viewsets.ModelViewSet):
    queryset = Cliente.objects.all()
    serializer_class = ClienteSerializer

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)


class ProductoViewSet(viewsets.ModelViewSet):
    queryset = Producto.objects.all()
    serializer_class = ProductoSerializer

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)


class EntradaViewSet(viewsets.ModelViewSet):
    queryset = Entrada.objects.select_related('proveedor', 'creado_por').prefetch_related('detalles__producto')
    serializer_class = EntradaSerializer

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)


class LoteGeneralViewSet(viewsets.ModelViewSet):
    queryset = LoteGeneral.objects.all()
    serializer_class = LoteGeneralSerializer


class EntradaDetalleViewSet(viewsets.ModelViewSet):
    queryset = EntradaDetalle.objects.all()
    serializer_class = EntradaDetalleSerializer


class SalidaViewSet(viewsets.ModelViewSet):
    queryset = Salida.objects.select_related('cliente', 'creado_por').prefetch_related('detalles__producto')
    serializer_class = SalidaSerializer

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)


class SalidaDetalleViewSet(viewsets.ModelViewSet):
    queryset = SalidaDetalle.objects.all()
    serializer_class = SalidaDetalleSerializer


class MovimientoCamaraViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    queryset = MovimientoCamara.objects.select_related(
        'entrada_detalle_origen__producto', 'entrada_detalle_destino',
        'camara_origen', 'camara_destino', 'salida_detalle', 'creado_por',
    )
    serializer_class = MovimientoCamaraSerializer

    def create(self, request, *args, **kwargs):
        entrada_serializer = MovimientoCamaraCrearSerializer(data=request.data)
        entrada_serializer.is_valid(raise_exception=True)
        movimiento = entrada_serializer.save(creado_por=request.user)
        salida_serializer = self.get_serializer(movimiento)
        return Response(salida_serializer.data, status=status.HTTP_201_CREATED)
