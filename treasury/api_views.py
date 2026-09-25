from accounts.permissions import EsSuperusuario
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count
from rest_framework import mixins, viewsets, status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from security.permissions import AreaTesoreria
from .models import (
    ArqueoCaja,
    Banco,
    ConfiguracionFolio,
    Denominacion,
    Divisa,
    CorteCaja,
    Empleado,
    MovimientoArchivo,
    NominaSemanal,
    Puesto,
    Rancho,
    SaldoCaja,
    MovimientoTesoreria,
)
from .api_serializers import (
    ArqueoCajaSerializer,
    BancoSerializer,
    CorteCajaSerializer,
    DenominacionSerializer,
    DivisaSerializer,
    EmpleadoSerializer,
    MovimientoArchivoSerializer,
    MovimientoTesoreriaSerializer,
    NominaSemanalSerializer,
    PuestoSerializer,
    RanchoSerializer,
    SaldoCajaSerializer,
)


def _mensaje_de(exc: DjangoValidationError) -> str:
    return exc.messages[0] if getattr(exc, "messages", None) else str(exc)


# Campos de MovimientoTesoreria habilitados para autocompletar en el formulario
# (no son catálogos aparte, solo valores ya usados en movimientos anteriores).
CAMPOS_SUGERENCIA = ("beneficiario", "autorizo", "concepto")
LIMITE_SUGERENCIAS = 10


class DivisaViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaTesoreria]
    queryset = Divisa.objects.all()
    serializer_class = DivisaSerializer


class CorteCajaViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaTesoreria]
    # El serializer anida los dos responsables y los saldos con su divisa.
    queryset = (
        CorteCaja.objects
        .select_related('responsable_apertura', 'responsable_cierre')
        .prefetch_related('saldos__divisa')
        .order_by('-fecha')
    )
    serializer_class = CorteCajaSerializer

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        corte = self.get_object()
        if corte.cerrado:
            return Response({'detail': 'El corte ya está cerrado.'}, status=status.HTTP_400_BAD_REQUEST)
        responsable = request.data.get('responsable_cierre')
        fecha_cierre = request.data.get('fecha_cierre')
        try:
            corte.close(responsable_cierre=request.user, fecha_cierre=fecha_cierre)
        except DjangoValidationError as exc:
            return Response({'detail': _mensaje_de(exc)}, status=status.HTTP_400_BAD_REQUEST)

        # El físico de cada divisa se toma del arqueo más reciente de este corte
        # (si se hizo alguno); si no hubo arqueo, saldo_fisico queda sin capturar.
        ultimo_arqueo = corte.arqueos.order_by('-hora_termino').first()
        if ultimo_arqueo:
            for arqueo_divisa in ultimo_arqueo.divisas.select_related('divisa'):
                saldo = corte.saldos.filter(divisa=arqueo_divisa.divisa).first()
                if saldo:
                    saldo.registrar_saldo_fisico(arqueo_divisa.total_contado)

        return Response(self.get_serializer(corte).data)


class MovimientoTesoreriaViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaTesoreria]
    queryset = MovimientoTesoreria.objects.prefetch_related('divisas__divisa').order_by('-fecha', '-creado')
    serializer_class = MovimientoTesoreriaSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        corte_id = self.request.query_params.get('corte')
        if corte_id:
            queryset = queryset.filter(corte_id=corte_id)
        return queryset

    @action(detail=True, methods=['post'])
    def cancelar(self, request, pk=None):
        movimiento = self.get_object()
        motivo = request.data.get('motivo', '')
        try:
            movimiento.cancelar(usuario=request.user, motivo=motivo)
        except DjangoValidationError as exc:
            return Response({'detail': _mensaje_de(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(movimiento).data)

    @action(detail=False, methods=['get'])
    def siguiente_folio(self, request):
        tipo = request.query_params.get('tipo')
        if tipo not in dict(MovimientoTesoreria.TIPO_CHOICES):
            return Response({'detail': 'Tipo inválido. Usa "I" o "E".'}, status=status.HTTP_400_BAD_REQUEST)
        config, _ = ConfiguracionFolio.objects.get_or_create(tipo=tipo, defaults={'siguiente_folio': 1})
        return Response({'folio': str(config.siguiente_folio)})

    @action(detail=False, methods=['get'])
    def sugerencias(self, request):
        campo = request.query_params.get('campo')
        if campo not in CAMPOS_SUGERENCIA:
            return Response(
                {'detail': f"Campo inválido. Usa uno de: {', '.join(CAMPOS_SUGERENCIA)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        texto = request.query_params.get('q', '').strip()
        queryset = MovimientoTesoreria.objects.exclude(**{campo: ''})
        if texto:
            queryset = queryset.filter(**{f'{campo}__icontains': texto})

        resultados = (
            queryset.values(campo)
            .annotate(total=Count('id'))
            .order_by('-total', campo)[:LIMITE_SUGERENCIAS]
        )
        return Response([fila[campo] for fila in resultados])


class SaldoCajaViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [AreaTesoreria]
    queryset = SaldoCaja.objects.select_related('divisa', 'corte').all()
    serializer_class = SaldoCajaSerializer


class DenominacionViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaTesoreria]
    # Sin filtrar por activa: el admin de denominaciones necesita ver también
    # las inactivas. Las pantallas que solo quieren las activas (p. ej. Arqueo)
    # deben pedirlo explícitamente con ?activa=true.
    queryset = Denominacion.objects.select_related('divisa').order_by('divisa__codigo', 'tipo', '-valor')
    serializer_class = DenominacionSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        divisa_id = self.request.query_params.get('divisa')
        if divisa_id:
            queryset = queryset.filter(divisa_id=divisa_id)
        activa = self.request.query_params.get('activa')
        if activa is not None:
            queryset = queryset.filter(activa=activa.lower() in ('1', 'true', 'si', 'sí'))
        return queryset

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [EsSuperusuario()]
        return super().get_permissions()


class ArqueoCajaViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaTesoreria]
    queryset = ArqueoCaja.objects.select_related('corte', 'usuario').prefetch_related('divisas__divisa', 'divisas__conteos__denominacion')
    serializer_class = ArqueoCajaSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        corte_id = self.request.query_params.get('corte')
        if corte_id:
            queryset = queryset.filter(corte_id=corte_id)
        return queryset

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.corte.cerrado:
            return Response({'detail': 'No se puede eliminar un arqueo de un corte cerrado.'}, status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)


class RanchoViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaTesoreria]
    queryset = Rancho.objects.all()
    serializer_class = RanchoSerializer


class PuestoViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaTesoreria]
    queryset = Puesto.objects.all()
    serializer_class = PuestoSerializer


class BancoViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaTesoreria]
    queryset = Banco.objects.all()
    serializer_class = BancoSerializer


class EmpleadoViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaTesoreria]
    queryset = Empleado.objects.select_related('rancho', 'puesto', 'banco').all()
    serializer_class = EmpleadoSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        rancho_id = self.request.query_params.get('rancho')
        if rancho_id:
            queryset = queryset.filter(rancho_id=rancho_id)
        return queryset


class NominaSemanalViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaTesoreria]
    queryset = NominaSemanal.objects.select_related('creado_por', 'cerrada_por').prefetch_related(
        'detalles__empleado__rancho', 'detalles__empleado__puesto'
    )
    serializer_class = NominaSemanalSerializer

    @action(detail=True, methods=['post'])
    def cerrar(self, request, pk=None):
        nomina = self.get_object()
        try:
            nomina.close(usuario=request.user)
        except DjangoValidationError as exc:
            return Response({'detail': _mensaje_de(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(nomina).data)


class MovimientoArchivoViewSet(
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [AreaTesoreria]
    # Sin update: un adjunto se reemplaza subiendo uno nuevo y borrando el viejo.
    queryset = MovimientoArchivo.objects.select_related('movimiento', 'subido_por')
    serializer_class = MovimientoArchivoSerializer
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        queryset = super().get_queryset()
        movimiento_id = self.request.query_params.get('movimiento')
        if movimiento_id:
            queryset = queryset.filter(movimiento_id=movimiento_id)
        return queryset
