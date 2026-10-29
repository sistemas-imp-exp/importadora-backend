"""API de Utilidad real (Cartera): filas como la hoja Registro, captura de costos y gasto operativo."""
from datetime import date

from django.utils.dateparse import parse_date
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from inventario.models import SalidaDetalle
from security.permissions import AreaCartera

from .api_views import _excel
from .models import CambioCostoVenta, GastoOperativo
from .utilidad import construir_excel, guardar_costos, utilidad_real
from .ventas import lineas_venta


def _texto(valor):
    return None if valor is None else str(valor)


def _periodo(params):
    """desde/hasta (AAAA-MM-DD); por omisión, el mes en curso."""
    hoy = date.today()
    desde = parse_date(params.get('desde') or '') or hoy.replace(day=1)
    hasta = parse_date(params.get('hasta') or '') or hoy
    if desde > hasta:
        raise serializers.ValidationError({'desde': 'La fecha inicial no puede ser posterior a la final.'})
    return desde, hasta


class UtilidadRealView(APIView):
    """GET ?desde=&hasta=&cliente=&producto= (JSON, o Excel con formato='excel')."""
    permission_classes = [AreaCartera]
    formato = None

    def get(self, request):
        params = request.query_params
        desde, hasta = _periodo(params)
        datos = utilidad_real(desde, hasta, cliente=params.get('cliente') or None, producto=params.get('producto') or None)
        if self.formato == 'excel':
            titulo = f'Utilidad real del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}'
            return _excel(construir_excel(datos, titulo), f'utilidad_real_{desde:%Y%m%d}_{hasta:%Y%m%d}.xlsx')
        gasto_actual = GastoOperativo.objects.filter(vigente_desde__lte=hasta).first()
        return Response({
            'desde': desde, 'hasta': hasta,
            'gasto_por_kilo': _texto(gasto_actual.por_kilo) if gasto_actual else '0',
            'filas': [{**f, 'kilos': _texto(f['kilos']), 'precio': _texto(f['precio']), 'venta': _texto(f['venta']),
                       'costo_unitario': _texto(f['costo_unitario']), 'costo': _texto(f['costo']),
                       'utilidad': _texto(f['utilidad']), 'margen': _texto(f['margen']),
                       'gasto_por_kilo': _texto(f['gasto_por_kilo'])} for f in datos['filas']],
            'resumen': {k: _texto(v) if not isinstance(v, int) else v for k, v in datos['resumen'].items()},
        })


class CostoLineaSerializer(serializers.Serializer):
    linea_id = serializers.PrimaryKeyRelatedField(source='linea', queryset=SalidaDetalle.objects.all())
    costo_unitario = serializers.DecimalField(max_digits=14, decimal_places=4, min_value=0, allow_null=True)


class CostosVentaView(APIView):
    """POST {costos: [{linea_id, costo_unitario}]}: costo por venta; null vuelve al propuesto del lote."""
    permission_classes = [AreaCartera]

    def post(self, request):
        serializer = CostoLineaSerializer(data=request.data.get('costos') or [], many=True)
        serializer.is_valid(raise_exception=True)
        costos = serializer.validated_data
        if not costos:
            return Response({'detail': 'No hay costos que guardar.'}, status=status.HTTP_400_BAD_REQUEST)
        ventas = set(lineas_venta().filter(id__in=[c['linea'].id for c in costos]).values_list('id', flat=True))
        ajenas = [c['linea'].id for c in costos if c['linea'].id not in ventas]
        if ajenas:
            return Response({'detail': 'Solo se asigna costo a líneas de venta.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'cambios': guardar_costos(costos, request.user)})


class HistorialCostoView(APIView):
    permission_classes = [AreaCartera]

    def get(self, request, linea_id):
        return Response([
            {'fecha': c.fecha, 'anterior': _texto(c.anterior), 'nuevo': _texto(c.nuevo), 'usuario': c.usuario.username}
            for c in CambioCostoVenta.objects.filter(salida_detalle_id=linea_id).select_related('usuario')
        ])


class GastoOperativoSerializer(serializers.ModelSerializer):
    usuario = serializers.CharField(source='usuario.username', read_only=True, default=None)

    class Meta:
        model = GastoOperativo
        fields = ['id', 'por_kilo', 'vigente_desde', 'usuario', 'creado']


class GastosOperativosView(APIView):
    """GET vigencias; POST {por_kilo, vigente_desde} agrega una (no cambia los meses anteriores)."""
    permission_classes = [AreaCartera]

    def get(self, request):
        return Response(GastoOperativoSerializer(GastoOperativo.objects.select_related('usuario'), many=True).data)

    def post(self, request):
        serializer = GastoOperativoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(usuario=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
