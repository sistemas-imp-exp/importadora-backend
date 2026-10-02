from accounts.permissions import EsSuperusuario
from django.core.exceptions import ValidationError as DjangoValidationError
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_date
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
    Empleado,
    MovimientoArchivo,
    NominaSemanal,
    Puesto,
    Rancho,
    MovimientoTesoreria,
    SaldoInicial,
)
from .api_serializers import (
    ArqueoCajaSerializer,
    BancoSerializer,
    DenominacionSerializer,
    DivisaSerializer,
    EmpleadoSerializer,
    GuardarSaldosInicialesSerializer,
    MovimientoArchivoSerializer,
    MovimientoTesoreriaSerializer,
    NominaSemanalSerializer,
    PuestoSerializer,
    RanchoSerializer,
    UsuarioResponsableSerializer,
)
from .saldos import historial, resumen_dia


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


def _fecha_param(request, nombre, por_defecto):
    texto = (request.query_params.get(nombre) or '').strip()
    if not texto:
        return por_defecto, None
    fecha = parse_date(texto)
    if fecha is None:
        return None, Response({nombre: 'Fecha inválida, usa AAAA-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)
    return fecha, None


def _dinero(valor):
    """Importe como texto con dos decimales, igual que los DecimalField del resto del API."""
    return str(Decimal(valor).quantize(Decimal('0.01')))


def _divisa_dict(divisa):
    return {'id': divisa.id, 'codigo': divisa.codigo, 'simbolo': divisa.simbolo, 'nombre': divisa.nombre}


class SaldoInicialViewSet(viewsets.ViewSet):
    """
    Saldo inicial de caja por divisa (sin fecha), base de todos los saldos
    calculados. Lo consulta Tesorería; solo el superusuario lo modifica.
    """
    permission_classes = [AreaTesoreria]

    def get_permissions(self):
        if self.action == 'create':
            return [EsSuperusuario()]
        return super().get_permissions()

    def _listado(self):
        saldos = {s.divisa_id: s for s in SaldoInicial.objects.select_related('editado_por')}
        divisas = Divisa.objects.filter(activa=True) | Divisa.objects.filter(id__in=saldos)
        return [
            {
                'divisa': _divisa_dict(divisa),
                'monto': _dinero(saldos[divisa.id].monto) if divisa.id in saldos else _dinero(0),
                'editado_por': UsuarioResponsableSerializer(saldos[divisa.id].editado_por).data if divisa.id in saldos else None,
                'editado_en': saldos[divisa.id].editado_en if divisa.id in saldos else None,
            }
            for divisa in divisas.order_by('codigo')
        ]

    def list(self, request):
        return Response(self._listado())

    def create(self, request):
        """Guarda los montos enviados (uno por divisa) y devuelve el listado completo."""
        serializer = GuardarSaldosInicialesSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(self._listado())


class CajaDiariaViewSet(viewsets.ViewSet):
    """
    Consulta de caja por día: saldos calculados con todos los movimientos
    hasta ese día, ver treasury/saldos.py.
    """
    permission_classes = [AreaTesoreria]

    @action(detail=False, methods=['get'])
    def dia(self, request):
        fecha, error = _fecha_param(request, 'fecha', timezone.localdate())
        if error:
            return error
        filas = resumen_dia(fecha)
        movimientos = (
            MovimientoTesoreria.objects.filter(fecha=fecha)
            .select_related('usuario', 'editado_por', 'usuario_cancelacion')
            .prefetch_related('divisas__divisa')
            .order_by('creado')
        )
        return Response({
            'fecha': fecha.isoformat(),
            'saldos': [
                {
                    'divisa': _divisa_dict(f['divisa']),
                    'saldo_inicial': _dinero(f['saldo_inicial']),
                    'ingresos': _dinero(f['ingresos']),
                    'egresos': _dinero(f['egresos']),
                    'saldo_final': _dinero(f['saldo_final']),
                    'negativo': f['negativo'],
                }
                for f in filas
            ],
            'negativo': any(f['negativo'] for f in filas),
            'movimientos': MovimientoTesoreriaSerializer(movimientos, many=True, context={'request': request}).data,
        })

    @action(detail=False, methods=['get'])
    def historial(self, request):
        hoy = timezone.localdate()
        hasta, error = _fecha_param(request, 'hasta', hoy)
        if error:
            return error
        desde, error = _fecha_param(request, 'desde', hasta - timedelta(days=30))
        if error:
            return error
        if desde > hasta:
            return Response({'desde': 'La fecha inicial no puede ser posterior a la final.'}, status=status.HTTP_400_BAD_REQUEST)
        divisas = {d.id: d for d in Divisa.objects.all()}
        dias = historial(desde, hasta)
        return Response([
            {
                'fecha': d['fecha'].isoformat(),
                'movimientos': d['movimientos'],
                'negativo': d['negativo'],
                'saldos': [
                    {
                        'divisa': _divisa_dict(divisas[divisa_id]),
                        'ingresos': _dinero(d['ingresos'].get(divisa_id, 0)),
                        'egresos': _dinero(d['egresos'].get(divisa_id, 0)),
                        'saldo_final': _dinero(saldo),
                    }
                    for divisa_id, saldo in sorted(d['saldos'].items(), key=lambda kv: divisas[kv[0]].codigo)
                ],
            }
            for d in reversed(dias)
        ])


class MovimientoTesoreriaViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaTesoreria]
    queryset = MovimientoTesoreria.objects.prefetch_related('divisas__divisa').order_by('-fecha', '-creado')
    serializer_class = MovimientoTesoreriaSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        fecha = parse_date(params.get('fecha') or '')
        if fecha:
            queryset = queryset.filter(fecha=fecha)
        desde = parse_date(params.get('desde') or '')
        if desde:
            queryset = queryset.filter(fecha__gte=desde)
        hasta = parse_date(params.get('hasta') or '')
        if hasta:
            queryset = queryset.filter(fecha__lte=hasta)
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
    queryset = ArqueoCaja.objects.select_related('usuario', 'editado_por').prefetch_related('divisas__divisa', 'divisas__conteos__denominacion')
    serializer_class = ArqueoCajaSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        fecha = parse_date(self.request.query_params.get('fecha') or '')
        if fecha:
            queryset = queryset.filter(fecha=fecha)
        return queryset


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
    queryset = MovimientoArchivo.objects.filter(eliminado=False).select_related('movimiento', 'subido_por')
    serializer_class = MovimientoArchivoSerializer
    parser_classes = [MultiPartParser, FormParser]

    def perform_destroy(self, instance):
        # No se borra: queda registrado quién lo quitó y cuándo (el archivo se conserva).
        instance.eliminado = True
        instance.eliminado_por = self.request.user
        instance.eliminado_en = timezone.now()
        instance.save(update_fields=['eliminado', 'eliminado_por', 'eliminado_en'])

    def get_queryset(self):
        queryset = super().get_queryset()
        movimiento_id = self.request.query_params.get('movimiento')
        if movimiento_id:
            queryset = queryset.filter(movimiento_id=movimiento_id)
        return queryset
