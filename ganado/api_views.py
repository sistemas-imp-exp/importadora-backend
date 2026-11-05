from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from inventario.paginacion import PaginacionInventario
from security.permissions import AreaGanado
from treasury.api_serializers import UsuarioResponsableSerializer

from . import reportes, servicio
from .api_serializers import (
    AnimalSerializer, CancelarSerializer, CategoriaSerializer, EditarMovimientoSerializer, EspecieSerializer,
    MovimientoSerializer, RanchoSerializer,
)
from .models import ACTIVO, CANCELADO, MOTIVOS, TIPOS, Animal, Categoria, Especie, Movimiento, Rancho

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _entero(valor, nombre):
    if valor in (None, ''):
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        raise ValidationError({nombre: 'Valor no válido.'})


def _fecha(valor, nombre, defecto=None):
    if not valor:
        if defecto is None:
            raise ValidationError({nombre: 'Indica la fecha (AAAA-MM-DD).'})
        return defecto
    fecha = parse_date(valor)
    if not fecha:
        raise ValidationError({nombre: 'La fecha no es válida. Usa el formato AAAA-MM-DD.'})
    return fecha


class RanchoViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaGanado]
    serializer_class = RanchoSerializer
    pagination_class = None

    def get_queryset(self):
        return Rancho.objects.annotate(cabezas=Count('animales', filter=Q(animales__estado=ACTIVO)))


class EspecieViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaGanado]
    serializer_class = EspecieSerializer
    queryset = Especie.objects.all()
    pagination_class = None


class CategoriaViewSet(viewsets.ModelViewSet):
    permission_classes = [AreaGanado]
    serializer_class = CategoriaSerializer
    queryset = Categoria.objects.select_related('especie')
    pagination_class = None


class AnimalViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                    viewsets.GenericViewSet):
    """
    Los animales se crean con un alta (movimientos/) y nunca se borran; aquí
    se consultan y se corrigen sus datos (arete, categoría…), con bitácora.
    Filtros: q (arete o folio), rancho, especie, categoria, sexo,
    estado (vacío = todos menos altas canceladas), arete=con|sin.
    """
    permission_classes = [AreaGanado]
    serializer_class = AnimalSerializer
    pagination_class = PaginacionInventario

    def get_queryset(self):
        animales = Animal.objects.select_related('especie', 'categoria', 'rancho')
        if self.action != 'list':
            return animales
        p = self.request.query_params
        estado = p.get('estado', ACTIVO)
        animales = animales.filter(estado=estado) if estado else animales.exclude(estado=CANCELADO)
        for campo in ('rancho', 'especie', 'categoria'):
            valor = _entero(p.get(campo), campo)
            if valor:
                animales = animales.filter(**{f'{campo}_id': valor})
        if p.get('sexo'):
            animales = animales.filter(sexo=p['sexo'])
        if p.get('arete') == 'con':
            animales = animales.exclude(arete_3='')
        elif p.get('arete') == 'sin':
            animales = animales.filter(arete_3='')
        for palabra in (p.get('q') or '').split():
            animales = animales.filter(
                Q(arete_1__icontains=palabra) | Q(arete_2__icontains=palabra) | Q(arete_3__icontains=palabra)
                | Q(folio__icontains=palabra) | Q(raza__icontains=palabra) | Q(notas__icontains=palabra)
            )
        return animales.order_by('rancho__nombre', 'especie__nombre', 'categoria__orden', 'categoria__nombre',
                                 'arete_2', 'arete_3', 'id')

    @action(detail=False)
    def seleccion(self, request):
        """Animales activos de un rancho, completos y compactos, para elegir en bajas y traspasos."""
        rancho = _entero(request.query_params.get('rancho'), 'rancho')
        if not rancho:
            raise ValidationError({'rancho': 'Indica el rancho.'})
        animales = (Animal.objects.filter(rancho_id=rancho, estado=ACTIVO)
                    .select_related('especie', 'categoria')
                    .order_by('especie__nombre', 'categoria__orden', 'categoria__nombre', 'arete_2', 'arete_3', 'id'))
        return Response([{
            'id': a.pk, 'folio': a.folio, 'arete': a.arete, 'identificacion': a.identificacion,
            'especie': a.especie.nombre, 'categoria': a.categoria.nombre, 'sexo': a.sexo, 'raza': a.raza,
        } for a in animales])

    @action(detail=True)
    def historial(self, request, pk=None):
        """Línea de tiempo del animal (lo más reciente primero): movimientos, cancelados incluidos, y ediciones."""
        animal = self.get_object()
        eventos = []
        movimientos = (Movimiento.objects.filter(lineas__animal=animal)
                       .select_related('rancho', 'rancho_destino', 'usuario', 'usuario_cancelacion'))
        for m in movimientos:
            eventos.append({
                'clase': 'movimiento', 'orden': (m.fecha.isoformat(), 1, m.pk), 'fecha': m.fecha.isoformat(),
                'id': m.pk, 'folio': m.folio, 'tipo': m.tipo, 'tipo_nombre': m.get_tipo_display(),
                'motivo_nombre': m.get_motivo_display() if m.motivo else '',
                'rancho': m.rancho.nombre, 'rancho_destino': m.rancho_destino.nombre if m.rancho_destino else '',
                'contraparte': m.contraparte, 'notas': m.notas,
                'usuario': UsuarioResponsableSerializer(m.usuario).data,
                'cancelado': m.cancelado, 'motivo_cancelacion': m.motivo_cancelacion,
            })
        for e in animal.ediciones.select_related('usuario'):
            fecha = timezone.localtime(e.fecha)
            eventos.append({
                'clase': 'edicion', 'orden': (fecha.date().isoformat(), 2, e.pk), 'fecha': fecha.date().isoformat(),
                'momento': e.fecha, 'cambios': e.cambios, 'usuario': UsuarioResponsableSerializer(e.usuario).data,
            })
        eventos.sort(key=lambda e: e.pop('orden'), reverse=True)
        return Response({'animal': AnimalSerializer(animal).data, 'eventos': eventos})


class MovimientoViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                        mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """
    Altas, bajas y traspasos. Se editan solo fecha, contraparte, importe y
    notas; para corregir animales o ranchos se cancela (el último movimiento
    de cada animal) y se vuelve a capturar. Filtros: desde, hasta, rancho
    (origen o destino), tipo, motivo, q (folio, contraparte, notas, arete),
    cancelados=1 para incluirlos.
    """
    permission_classes = [AreaGanado]
    serializer_class = MovimientoSerializer
    pagination_class = PaginacionInventario

    def get_queryset(self):
        movimientos = (Movimiento.objects
                       .select_related('rancho', 'rancho_destino', 'usuario', 'editado_por', 'usuario_cancelacion')
                       .prefetch_related('lineas__animal__categoria', 'lineas__animal__especie'))
        if self.action != 'list':
            return movimientos
        p = self.request.query_params
        if p.get('cancelados') not in ('1', 'true'):
            movimientos = movimientos.filter(cancelado=False)
        if p.get('desde'):
            movimientos = movimientos.filter(fecha__gte=_fecha(p['desde'], 'desde'))
        if p.get('hasta'):
            movimientos = movimientos.filter(fecha__lte=_fecha(p['hasta'], 'hasta'))
        rancho = _entero(p.get('rancho'), 'rancho')
        if rancho:
            movimientos = movimientos.filter(Q(rancho_id=rancho) | Q(rancho_destino_id=rancho))
        if p.get('tipo'):
            movimientos = movimientos.filter(tipo=p['tipo'])
        if p.get('motivo'):
            movimientos = movimientos.filter(motivo=p['motivo'])
        for palabra in (p.get('q') or '').split():
            filtro = (Q(contraparte__icontains=palabra) | Q(notas__icontains=palabra)
                      | Q(lineas__animal__arete_3__icontains=palabra) | Q(lineas__animal__folio__icontains=palabra))
            numero = palabra.upper().removeprefix('MG-')
            if numero.isdigit():
                filtro |= Q(pk=int(numero))
            movimientos = movimientos.filter(pk__in=Movimiento.objects.filter(filtro).values('pk'))
        return movimientos.order_by('-fecha', '-id')

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'detalle': self.action != 'list'}

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        datos = dict(serializer.validated_data)
        animales = datos.pop('animales', [])
        animal_ids = datos.pop('animal_ids', [])
        movimiento = servicio.registrar(datos, animales, animal_ids, request.user)
        movimiento = self.get_queryset().get(pk=movimiento.pk)
        return Response(self.get_serializer(movimiento).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        movimiento = self.get_object()
        serializer = EditarMovimientoSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        servicio.editar(movimiento, serializer.validated_data, request.user)
        return Response(self.get_serializer(self.get_queryset().get(pk=movimiento.pk)).data)

    @action(detail=True, methods=['post'])
    def cancelar(self, request, pk=None):
        movimiento = self.get_object()
        serializer = CancelarSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        servicio.cancelar(movimiento, serializer.validated_data['motivo'], request.user)
        return Response(self.get_serializer(self.get_queryset().get(pk=movimiento.pk)).data)


def _descarga(contenido, tipo, nombre):
    respuesta = HttpResponse(contenido, content_type=tipo)
    respuesta['Content-Disposition'] = f'attachment; filename="{nombre}"'
    return respuesta


def _excel(libro, nombre):
    respuesta = HttpResponse(content_type=XLSX)
    respuesta['Content-Disposition'] = f'attachment; filename="{nombre}"'
    libro.save(respuesta)
    return respuesta


def _descripcion_filtros(rancho=None, especie=None, tipo=None, motivo=None):
    partes = []
    if rancho:
        partes.append(f"Rancho: {Rancho.objects.filter(pk=rancho).values_list('nombre', flat=True).first() or rancho}")
    if especie:
        partes.append(f"Especie: {Especie.objects.filter(pk=especie).values_list('nombre', flat=True).first() or especie}")
    if tipo:
        partes.append(f'Tipo: {dict(TIPOS).get(tipo, tipo)}')
    if motivo:
        partes.append(f'Motivo: {dict(MOTIVOS).get(motivo, motivo)}')
    return ' · '.join(partes)


class InventarioView(APIView):
    """Cabezas por rancho a una fecha (?fecha=, hoy por defecto; ?rancho=, ?especie=)."""
    permission_classes = [AreaGanado]
    formato = None

    def get(self, request):
        p = request.query_params
        fecha = _fecha(p.get('fecha'), 'fecha', timezone.localdate())
        rancho = _entero(p.get('rancho'), 'rancho')
        especie = _entero(p.get('especie'), 'especie')
        datos = reportes.inventario(fecha, rancho, especie)
        nombre = f'inventario_ganado_{fecha:%Y%m%d}'
        if self.formato == 'excel':
            return _excel(reportes.excel_inventario(datos), f'{nombre}.xlsx')
        if self.formato == 'pdf':
            pdf = reportes.pdf_inventario(datos, _descripcion_filtros(rancho, especie))
            return _descarga(pdf.getvalue(), 'application/pdf', f'{nombre}.pdf')
        return Response(reportes.serializar_inventario(datos, con_animales=p.get('animales') in ('1', 'true')))


class ReporteMovimientosView(APIView):
    """Balance y movimientos de un periodo (?desde=&hasta= obligatorios; ?rancho=&tipo=&motivo=)."""
    permission_classes = [AreaGanado]
    formato = None

    def get(self, request):
        p = request.query_params
        desde = _fecha(p.get('desde'), 'desde')
        hasta = _fecha(p.get('hasta'), 'hasta')
        if desde > hasta:
            raise ValidationError({'hasta': 'El rango de fechas está invertido.'})
        rancho = _entero(p.get('rancho'), 'rancho')
        tipo, motivo = p.get('tipo') or None, p.get('motivo') or None
        datos = reportes.movimientos(desde, hasta, rancho, tipo, motivo)
        nombre = f'movimientos_ganado_{desde:%Y%m%d}_{hasta:%Y%m%d}'
        if self.formato == 'excel':
            return _excel(reportes.excel_movimientos(datos), f'{nombre}.xlsx')
        if self.formato == 'pdf':
            pdf = reportes.pdf_movimientos(datos, _descripcion_filtros(rancho, None, tipo, motivo))
            return _descarga(pdf.getvalue(), 'application/pdf', f'{nombre}.pdf')
        return Response(reportes.serializar_movimientos(datos))
