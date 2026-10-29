from django.core.validators import RegexValidator
from rest_framework import serializers

from security.models import Area

from .models import Notificacion

# Solo rutas internas del frontend: nada de esquemas (javascript:, http:...) ni
# "//otro.sitio" (el navegador lo toma como dirección externa).
validar_enlace = RegexValidator(r'^/(?!/)[A-Za-z0-9/_\-?=&.%]*$', 'El enlace debe ser una ruta del sistema.')


class AreaBreveSerializer(serializers.ModelSerializer):
    class Meta:
        model = Area
        fields = ['id', 'codigo', 'nombre']


def _autor(usuario):
    if usuario is None:
        return None
    return {'id': usuario.pk, 'username': usuario.username, 'nombre': usuario.get_full_name()}


class NotificacionSerializer(serializers.ModelSerializer):
    area_origen = AreaBreveSerializer(read_only=True)
    area_destino = AreaBreveSerializer(read_only=True)
    creada_por = serializers.SerializerMethodField()
    leida = serializers.SerializerMethodField()
    hilo = serializers.IntegerField(source='raiz_id', read_only=True)

    class Meta:
        model = Notificacion
        fields = [
            'id', 'hilo', 'tipo', 'evento', 'area_origen', 'area_destino', 'titulo', 'mensaje', 'enlace',
            'creada_por', 'creado', 'leida',
        ]

    def get_creada_por(self, obj):
        return _autor(obj.creada_por)

    def get_leida(self, obj):
        """Lo que yo escribí cuenta como leído; si no viene anotado, se toma del contexto."""
        user = self.context['request'].user
        if obj.creada_por_id == user.pk:
            return True
        if hasattr(obj, 'leida'):
            return obj.leida
        return obj.pk not in self.context.get('no_leidas', set())


class HiloSerializer(NotificacionSerializer):
    """Fila del listado: la primera notificación del hilo con su actividad."""
    respuestas = serializers.IntegerField(source='total_respuestas', read_only=True)
    ultima_actividad = serializers.DateTimeField(source='actividad', read_only=True)
    no_leidas = serializers.SerializerMethodField()

    class Meta(NotificacionSerializer.Meta):
        fields = NotificacionSerializer.Meta.fields + ['respuestas', 'ultima_actividad', 'no_leidas']

    def get_leida(self, obj):
        return self.get_no_leidas(obj) == 0

    def get_no_leidas(self, obj):
        return self.context.get('no_leidas_por_hilo', {}).get(obj.pk, 0)


class NuevoMensajeSerializer(serializers.Serializer):
    area_origen = serializers.SlugRelatedField(slug_field='codigo', queryset=Area.objects.filter(activo=True))
    area_destino = serializers.SlugRelatedField(slug_field='codigo', queryset=Area.objects.filter(activo=True))
    titulo = serializers.CharField(max_length=150)
    mensaje = serializers.CharField(max_length=4000)
    enlace = serializers.CharField(max_length=200, required=False, allow_blank=True, validators=[validar_enlace])


class RespuestaSerializer(serializers.Serializer):
    mensaje = serializers.CharField(max_length=4000)
    # Solo si quien responde pertenece a las dos áreas del hilo (p. ej. superusuario).
    area_origen = serializers.SlugRelatedField(
        slug_field='codigo', queryset=Area.objects.filter(activo=True), required=False,
    )
