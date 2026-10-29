from collections import Counter

from django.db.models import Count, Max, Q
from django.db.models.functions import Coalesce
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from inventario.paginacion import PaginacionInventario
from security.permissions import tiene_area

from .models import CanalArea, Notificacion
from .serializers import (
    AreaBreveSerializer,
    HiloSerializer,
    NotificacionSerializer,
    NuevoMensajeSerializer,
    RespuestaSerializer,
)
from .servicio import areas_de, canales_de, con_leida, marcar_leidas, no_leidas, puntos, visibles

RECIENTES_EN_CAMPANA = 8


def _no_leidas_por_hilo(user):
    return Counter(hilo or pk for pk, hilo in no_leidas(user).values_list('pk', 'hilo_id'))


class NotificacionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    Hilos de avisos y mensajes entre áreas. Todo usuario autenticado entra; cada
    quien ve los de sus áreas (el superusuario, todos). Marcar como leído es un
    POST pero no requiere escritura en el área: es estado personal.
    """
    serializer_class = HiloSerializer
    pagination_class = PaginacionInventario

    def get_queryset(self):
        return visibles(self.request.user)

    def list(self, request, *args, **kwargs):
        user = request.user
        queryset = (
            self.get_queryset().filter(hilo__isnull=True)
            .annotate(total_respuestas=Count('respuestas'), actividad=Coalesce(Max('respuestas__creado'), 'creado'))
            .order_by('-actividad', '-pk')
        )
        params = request.query_params
        if params.get('area'):
            queryset = queryset.filter(Q(area_origen__codigo=params['area']) | Q(area_destino__codigo=params['area']))
        if params.get('tipo') in (Notificacion.AVISO, Notificacion.MENSAJE):
            queryset = queryset.filter(tipo=params['tipo'])
        por_hilo = _no_leidas_por_hilo(user)
        if params.get('no_leidas') in ('1', 'true'):
            queryset = queryset.filter(pk__in=list(por_hilo))
        contexto = {**self.get_serializer_context(), 'no_leidas_por_hilo': por_hilo}
        pagina = self.paginate_queryset(queryset)
        return self.get_paginated_response(HiloSerializer(pagina, many=True, context=contexto).data)

    def retrieve(self, request, *args, **kwargs):
        raiz = self.get_object()
        if raiz.hilo_id:
            raiz = self.get_queryset().get(pk=raiz.hilo_id)
        mensajes = con_leida(
            Notificacion.objects.filter(Q(pk=raiz.pk) | Q(hilo=raiz)), request.user,
        ).select_related('area_origen', 'area_destino', 'creada_por').order_by('creado', 'pk')
        return Response({
            'id': raiz.pk,
            'titulo': raiz.titulo,
            'areas': AreaBreveSerializer([raiz.area_origen, raiz.area_destino], many=True).data,
            'mensajes': NotificacionSerializer(mensajes, many=True, context=self.get_serializer_context()).data,
            'responder_como': AreaBreveSerializer(self._areas_para_responder(raiz), many=True).data,
        })

    def create(self, request):
        serializer = NuevoMensajeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        datos = serializer.validated_data
        origen, destino = datos['area_origen'], datos['area_destino']
        if not tiene_area(request.user, origen.codigo, escritura=True):
            raise PermissionDenied(f'No puedes escribir a nombre de {origen.nombre}.')
        if not CanalArea.permitido(origen, destino):
            raise ValidationError({'area_destino': f'{origen.nombre} no tiene comunicación con {destino.nombre}.'})
        notificacion = Notificacion.objects.create(
            tipo=Notificacion.MENSAJE, area_origen=origen, area_destino=destino, titulo=datos['titulo'],
            mensaje=datos['mensaje'], enlace=datos.get('enlace', ''), creada_por=request.user,
        )
        return Response(
            NotificacionSerializer(notificacion, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    def _areas_para_responder(self, raiz):
        return [
            area for area in (raiz.area_origen, raiz.area_destino)
            if tiene_area(self.request.user, area.codigo, escritura=True)
        ]

    @action(detail=True, methods=['post'])
    def responder(self, request, pk=None):
        raiz = self.get_object()
        if raiz.hilo_id:
            raiz = self.get_queryset().get(pk=raiz.hilo_id)
        serializer = RespuestaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        posibles = self._areas_para_responder(raiz)
        if not posibles:
            raise PermissionDenied('No tienes permiso de escritura en ninguna de las áreas de esta conversación.')
        origen = serializer.validated_data.get('area_origen')
        if origen is None:
            if len(posibles) > 1:
                raise ValidationError({'area_origen': 'Elige a nombre de qué área respondes.'})
            origen = posibles[0]
        elif origen not in posibles:
            raise ValidationError({'area_origen': 'Esa área no participa en la conversación.'})
        destino = raiz.area_destino if origen == raiz.area_origen else raiz.area_origen
        if not CanalArea.permitido(origen, destino):
            raise ValidationError('La comunicación entre estas áreas está desactivada.')
        respuesta = Notificacion.objects.create(
            tipo=Notificacion.MENSAJE, area_origen=origen, area_destino=destino, hilo=raiz,
            titulo=raiz.titulo, mensaje=serializer.validated_data['mensaje'], enlace=raiz.enlace,
            creada_por=request.user,
        )
        # Responder implica haber leído el hilo.
        marcar_leidas(request.user, Notificacion.objects.filter(Q(pk=raiz.pk) | Q(hilo=raiz)))
        return Response(
            NotificacionSerializer(respuesta, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'])
    def leer(self, request, pk=None):
        raiz = self.get_object()
        raiz_id = raiz.hilo_id or raiz.pk
        marcar_leidas(request.user, Notificacion.objects.filter(Q(pk=raiz_id) | Q(hilo_id=raiz_id)))
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['post'], url_path='leer-todas')
    def leer_todas(self, request):
        marcar_leidas(request.user, no_leidas(request.user))
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['get'])
    def resumen(self, request):
        """Lo que consulta la campana cada minuto: no leídas, las más recientes y los puntos del menú."""
        user = request.user
        pendientes = no_leidas(user)
        recientes = pendientes.select_related('area_origen', 'area_destino', 'creada_por').order_by('-creado')[
            :RECIENTES_EN_CAMPANA
        ]
        contexto = {**self.get_serializer_context(), 'no_leidas': {n.pk for n in recientes}}
        return Response({
            'no_leidas': pendientes.count(),
            'recientes': NotificacionSerializer(recientes, many=True, context=contexto).data,
            'puntos': puntos(user),
        })

    @action(detail=False, methods=['get'])
    def canales(self, request):
        """A qué áreas puede escribir el usuario, y desde cuál."""
        return Response([
            {'origen': AreaBreveSerializer(origen).data, 'destino': AreaBreveSerializer(destino).data}
            for origen, destino in canales_de(request.user)
        ])

    @action(detail=False, methods=['get'])
    def areas(self, request):
        """Áreas que el usuario puede usar como filtro en el listado."""
        from security.models import Area
        return Response(AreaBreveSerializer(Area.objects.filter(pk__in=areas_de(request.user)), many=True).data)
