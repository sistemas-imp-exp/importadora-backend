from django.db import transaction
from django.db.models import Exists, OuterRef, Prefetch, Q
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from security.permissions import AreaInventario

from .paginacion import PaginacionInventario

from .auditoria import entrada_tiene_salidas, registrar_eliminacion
from .models import (
    Camara,
    Cliente,
    EdicionEntrada,
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
    permission_classes = [AreaInventario]
    queryset = Empresa.objects.all()
    serializer_class = EmpresaSerializer

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)


class CamaraViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaInventario]
    # creado_por se serializa anidado en todos los catalogos: sin el select_related
    # cada fila del listado dispara una consulta extra por su usuario.
    queryset = Camara.objects.select_related('creado_por', 'empresa')
    serializer_class = CamaraSerializer

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)


class ProveedorViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaInventario]
    queryset = Proveedor.objects.select_related('creado_por')
    serializer_class = ProveedorSerializer

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)


class ClienteViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaInventario]
    queryset = Cliente.objects.select_related('creado_por')
    serializer_class = ClienteSerializer

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)


class ProductoViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaInventario]
    queryset = Producto.objects.select_related('creado_por')
    serializer_class = ProductoSerializer

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)


class EntradaViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaInventario]
    queryset = (
        Entrada.objects
        .select_related('proveedor__creado_por', 'creado_por')
        .prefetch_related(
            # Los detalles se traen con su consumo ya anotado y sus relaciones
            # resueltas: es lo que evita las miles de consultas por lote.
            Prefetch('detalles', queryset=EntradaDetalle.objects.con_consumo()),
            'lotes_generales',
        )
        .annotate(fue_editada=Exists(EdicionEntrada.objects.filter(entrada=OuterRef('pk'))))
    )
    serializer_class = EntradaSerializer
    pagination_class = PaginacionInventario

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        busqueda = (params.get('busqueda') or '').strip()
        if busqueda:
            # Cubre lo que el usuario tiene a la mano del documento físico y lo
            # que ve en la tabla. Toca detalles, así que hace falta distinct().
            queryset = queryset.filter(
                Q(factura__icontains=busqueda)
                | Q(pedimento__icontains=busqueda)
                | Q(proveedor__nombre__icontains=busqueda)
                | Q(lotes_generales__codigo__icontains=busqueda)
                | Q(detalles__lote_proveedor__icontains=busqueda)
                | Q(detalles__producto__talla__icontains=busqueda)
                | Q(detalles__producto__tipo__icontains=busqueda)
            ).distinct()

        desde = params.get('desde')
        if desde:
            queryset = queryset.filter(fecha__gte=desde)
        hasta = params.get('hasta')
        if hasta:
            queryset = queryset.filter(fecha__lte=hasta)

        return queryset

    @action(detail=False, methods=['get'])
    def resumen(self, request):
        """
        Lista ligera para selectores: id, fecha, proveedor y factura, sin lotes.

        La usa Auditoría de entradas, que necesita todas las entradas para su
        buscador pero ninguno de sus detalles hasta que se elige una.
        """
        entradas = (
            Entrada.objects
            .filter(proveedor__isnull=False)
            .select_related('proveedor')
            .annotate(fue_editada=Exists(EdicionEntrada.objects.filter(entrada=OuterRef('pk'))))
            .order_by('-fecha')
        )
        return Response([
            {
                'id': e.id,
                'fecha': e.fecha.isoformat(),
                'proveedor': e.proveedor.nombre,
                'factura': e.factura,
                'editado': e.fue_editada,
            }
            for e in entradas
        ])

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)

    def destroy(self, request, *args, **kwargs):
        entrada = self.get_object()

        if entrada_tiene_salidas(entrada):
            return Response(
                {'detail': (
                    'Esta entrada ya tiene salidas o movimientos entre cámaras registrados '
                    'y no puede eliminarse.'
                )},
                status=status.HTTP_400_BAD_REQUEST,
            )

        usuario = request.user if request.user.is_authenticated else None

        # Las líneas y el lote general apuntan a la entrada con on_delete=PROTECT,
        # así que hay que borrarlos en orden: sin esto, borrar cualquier entrada
        # con líneas lanza ProtectedError (error 500 en vez de una respuesta útil).
        with transaction.atomic():
            registrar_eliminacion(entrada, usuario)
            entrada.detalles.all().delete()
            entrada.lotes_generales.all().delete()
            entrada.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)


class LoteGeneralViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaInventario]
    queryset = LoteGeneral.objects.select_related('creado_por', 'camara', 'entrada')
    serializer_class = LoteGeneralSerializer


class EntradaDetalleViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaInventario]
    queryset = EntradaDetalle.objects.con_consumo()
    serializer_class = EntradaDetalleSerializer


class SalidaViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaInventario]
    queryset = (
        Salida.objects
        .select_related('cliente', 'creado_por')
        .prefetch_related(
            # entrada_detalle entra aquí porque cada línea publica el lote y el
            # proveedor de origen: antes la tabla los buscaba en el navegador
            # descargando TODAS las entradas.
            'detalles__producto',
            'detalles__entrada_detalle__proveedor_origen',
        )
    )
    serializer_class = SalidaSerializer
    pagination_class = PaginacionInventario

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        busqueda = (params.get('busqueda') or '').strip()
        if busqueda:
            queryset = queryset.filter(
                Q(folio_de_salida__icontains=busqueda)
                | Q(cliente__nombre__icontains=busqueda)
                | Q(notas__icontains=busqueda)
                | Q(detalles__producto__talla__icontains=busqueda)
                | Q(detalles__producto__tipo__icontains=busqueda)
            ).distinct()

        desde = params.get('desde')
        if desde:
            queryset = queryset.filter(fecha__gte=desde)
        hasta = params.get('hasta')
        if hasta:
            queryset = queryset.filter(fecha__lte=hasta)

        return queryset

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)


class SalidaDetalleViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaInventario]
    queryset = SalidaDetalle.objects.select_related('producto__creado_por', 'camara', 'salida')
    serializer_class = SalidaDetalleSerializer


class MovimientoCamaraViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [AreaInventario]
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
