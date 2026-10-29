"""
API de Resguardos (/api/resguardos/): la usan Inventario y Cartera por igual
(AreaInventarioOCartera), por eso vive fuera de /api/inventario/.
"""
from collections import defaultdict
from decimal import Decimal

from django.db.models import F, Sum
from django.shortcuts import get_object_or_404
from rest_framework import generics, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from security.permissions import AreaInventarioOCartera

from .existencias_views import _serializar as serializar_lote
from .models import Cliente, EntradaDetalle, Salida, SalidaDetalle
from .paginacion import PaginacionInventario
from .reportes import obtener_lotes_filtrados
from .resguardos import (
    cancelar_retiro, lineas_con_retirado, registrar_retiro, resguardos, retiros_de, serializar_resguardo,
    serializar_retiro,
)


class RetiroLineaSerializer(serializers.Serializer):
    retiro_de = serializers.PrimaryKeyRelatedField(queryset=SalidaDetalle.objects.filter(kilos_resguardo__gt=0))
    entrada_detalle = serializers.PrimaryKeyRelatedField(queryset=EntradaDetalle.objects.con_consumo())
    cajas = serializers.IntegerField(min_value=0)
    total_kilos = serializers.DecimalField(max_digits=15, decimal_places=3, min_value=Decimal('0.001'))


class RetiroResguardoSerializer(serializers.Serializer):
    venta = serializers.PrimaryKeyRelatedField(
        queryset=Salida.objects.filter(cliente__isnull=False, folio_retiro__isnull=True).select_related('cliente'),
    )
    fecha = serializers.DateField()
    # Folio de la salida de cámara y nota del retiro (documentos en papel).
    folio_de_salida = serializers.CharField(max_length=30)
    notas = serializers.CharField(max_length=100)
    detalles = RetiroLineaSerializer(many=True)

    def validate_folio_de_salida(self, value):
        folio = value.strip().upper()
        if Salida.objects.filter(folio_de_salida=folio).exists():
            raise serializers.ValidationError('Ya existe una salida con ese folio.')
        return folio

    def validate_notas(self, value):
        return value.strip().upper()

    def validate(self, data):
        venta, detalles = data['venta'], data['detalles']
        if not detalles:
            raise serializers.ValidationError({'detalles': 'Agrega al menos un producto a entregar.'})
        if data['fecha'] < venta.fecha:
            raise serializers.ValidationError({'fecha': 'El retiro no puede ser anterior a la venta.'})

        pendientes = {l.id: l.kilos_resguardo - l.kilos_retirados for l in lineas_con_retirado().filter(salida=venta)}
        por_linea, cajas_lote, kilos_lote, lotes = defaultdict(Decimal), defaultdict(int), defaultdict(Decimal), {}
        for item in detalles:
            linea, lote = item['retiro_de'], item['entrada_detalle']
            if linea.salida_id != venta.id:
                raise serializers.ValidationError({'detalles': 'Un retiro es de una sola venta: hay un producto de otra.'})
            # Puede salir de otro lote o proveedor, pero ha de ser el mismo producto.
            if lote.producto_id != linea.producto_id:
                raise serializers.ValidationError({
                    'detalles': f'El lote {lote.lote_proveedor} es de {lote.producto}, no de {linea.producto}.'
                })
            por_linea[linea.id] += item['total_kilos']
            lotes[lote.id] = lote
            cajas_lote[lote.id] += item['cajas']
            kilos_lote[lote.id] += item['total_kilos']

        for linea_id, kilos in por_linea.items():
            if kilos > pendientes.get(linea_id, Decimal('0')):
                linea = next(i['retiro_de'] for i in detalles if i['retiro_de'].id == linea_id)
                raise serializers.ValidationError({
                    'detalles': (
                        f'De {linea.producto} quedan {pendientes.get(linea_id, 0)} kg en resguardo; '
                        f'pediste {kilos} kg.'
                    )
                })
        for lote_id, lote in lotes.items():
            if cajas_lote[lote_id] > lote.cajas_disponibles or kilos_lote[lote_id] > lote.kilos_disponibles:
                raise serializers.ValidationError({
                    'detalles': (
                        f'No hay suficiente en el lote {lote.lote_proveedor} ({lote.producto}): '
                        f'disponibles {lote.cajas_disponibles} cajas · {lote.kilos_disponibles} kg.'
                    )
                })
        return data


class ResguardosView(generics.GenericAPIView):
    """Ventas con resguardo (?estado=pendiente|entregado|todos, ?cliente=, ?busqueda=), con sus retiros."""

    permission_classes = [AreaInventarioOCartera]
    pagination_class = PaginacionInventario

    def get(self, request):
        ventas, retiros = resguardos(request.query_params)
        pagina = self.paginate_queryset(ventas)
        por_venta = retiros_de(pagina, retiros)
        respuesta = self.get_paginated_response([serializar_resguardo(v, por_venta) for v in pagina])
        pendiente = ventas.aggregate(total=Sum(F('total_resguardo') - F('total_retirado')))['total']
        respuesta.data['kilos_pendientes'] = str((pendiente or Decimal('0')).quantize(Decimal('0.001')))
        return respuesta


class RetirosView(APIView):
    permission_classes = [AreaInventarioOCartera]

    def post(self, request):
        serializer = RetiroResguardoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        retiro = registrar_retiro(serializer.validated_data, request.user)
        retiro = Salida.objects.prefetch_related(
            'detalles__producto', 'detalles__entrada_detalle__proveedor_origen', 'detalles__camara',
        ).select_related('creado_por').get(pk=retiro.pk)
        return Response(serializar_retiro(retiro), status=status.HTTP_201_CREATED)


class RetiroDetalleView(APIView):
    permission_classes = [AreaInventarioOCartera]

    def delete(self, request, pk):
        retiro = get_object_or_404(Salida, pk=pk, folio_retiro__isnull=False)
        cancelar_retiro(retiro, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ClientesConResguardoView(APIView):
    """Clientes con al menos una venta en resguardo, para el filtro."""

    permission_classes = [AreaInventarioOCartera]

    def get(self, request):
        clientes = Cliente.objects.filter(
            salidas__folio_retiro__isnull=True, salidas__detalles__kilos_resguardo__gt=0,
        ).distinct().order_by('nombre')
        return Response([{'id': c.id, 'nombre': c.nombre} for c in clientes])


class LotesParaRetiroView(APIView):
    """Lotes con existencia de los productos indicados (?producto=1&producto=2): de dónde se entrega un retiro."""

    permission_classes = [AreaInventarioOCartera]

    def get(self, request):
        productos = [p for p in request.query_params.getlist('producto') if p.isdigit()]
        if not productos:
            return Response([])
        lotes = obtener_lotes_filtrados({}).filter(producto_id__in=productos)
        return Response([serializar_lote(lote) for lote in lotes])
