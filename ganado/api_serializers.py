from collections import Counter

from django.db import transaction
from rest_framework import serializers

from treasury.api_serializers import UsuarioResponsableSerializer

from .models import (
    MOTIVOS, SEXOS, TIPOS, Animal, Categoria, EdicionAnimal, Especie, Movimiento, Rancho,
)


class RanchoSerializer(serializers.ModelSerializer):
    cabezas = serializers.IntegerField(read_only=True)

    class Meta:
        model = Rancho
        fields = ['id', 'nombre', 'activo', 'notas', 'cabezas']

    def validate_nombre(self, valor):
        return valor.strip().upper()


class EspecieSerializer(serializers.ModelSerializer):
    class Meta:
        model = Especie
        fields = ['id', 'nombre', 'activo']

    def validate_nombre(self, valor):
        return valor.strip().upper()


class CategoriaSerializer(serializers.ModelSerializer):
    especie_id = serializers.PrimaryKeyRelatedField(source='especie', queryset=Especie.objects.all())
    especie_nombre = serializers.CharField(source='especie.nombre', read_only=True)

    class Meta:
        model = Categoria
        fields = ['id', 'especie_id', 'especie_nombre', 'nombre', 'sexo', 'orden', 'activo']
        validators = []  # la unicidad se explica en validate()

    def validate_nombre(self, valor):
        return valor.strip().upper()

    def validate(self, attrs):
        especie = attrs.get('especie', getattr(self.instance, 'especie', None))
        nombre = attrs.get('nombre', getattr(self.instance, 'nombre', None))
        repetida = Categoria.objects.filter(especie=especie, nombre=nombre)
        if self.instance:
            repetida = repetida.exclude(pk=self.instance.pk)
        if repetida.exists():
            raise serializers.ValidationError({'nombre': f'{especie} ya tiene la categoría {nombre}.'})
        if self.instance and 'especie' in attrs and attrs['especie'] != self.instance.especie \
                and self.instance.animales.exists():
            raise serializers.ValidationError({'especie_id': 'La categoría ya tiene animales: no cambia de especie.'})
        return attrs


def _ref(objeto):
    return {'id': objeto.pk, 'nombre': objeto.nombre} if objeto else None


class AnimalSerializer(serializers.ModelSerializer):
    """Datos del animal. Rancho y estado no se editan: los cambian los movimientos."""
    especie_id = serializers.PrimaryKeyRelatedField(source='especie', queryset=Especie.objects.all())
    categoria_id = serializers.PrimaryKeyRelatedField(source='categoria', queryset=Categoria.objects.all())

    class Meta:
        model = Animal
        fields = [
            'id', 'folio', 'arete_1', 'arete_2', 'arete_3', 'especie_id', 'categoria_id', 'sexo',
            'raza', 'fecha_nacimiento', 'notas',
        ]
        read_only_fields = ['folio']
        validators = []  # el arete repetido lo explica Animal.full_clean()

    def to_representation(self, animal):
        datos = super().to_representation(animal)
        datos.pop('especie_id')
        datos.pop('categoria_id')
        datos.update({
            'arete': animal.arete,
            'identificacion': animal.identificacion,
            'especie': _ref(animal.especie),
            'categoria': _ref(animal.categoria),
            'sexo_nombre': animal.get_sexo_display(),
            'rancho': _ref(animal.rancho),
            'estado': animal.estado,
            'estado_nombre': animal.get_estado_display(),
        })
        return datos

    @transaction.atomic
    def update(self, animal, validated_data):
        antes = {campo: _texto(animal, campo) for campo in validated_data}
        for campo, valor in validated_data.items():
            setattr(animal, campo, valor)
        animal.full_clean(exclude=['folio', 'usuario'])
        animal.save()
        cambios = [
            {'campo': ETIQUETAS[campo], 'antes': antes[campo], 'despues': _texto(animal, campo)}
            for campo in validated_data if antes[campo] != _texto(animal, campo)
        ]
        if cambios:
            EdicionAnimal.objects.create(animal=animal, usuario=self.context['request'].user, cambios=cambios)
        return animal


ETIQUETAS = {
    'arete_1': 'Arete (1)', 'arete_2': 'Arete (2)', 'arete_3': 'Arete (3)', 'especie': 'Especie',
    'categoria': 'Categoría', 'sexo': 'Sexo', 'raza': 'Raza / color', 'fecha_nacimiento': 'Nacimiento',
    'notas': 'Notas',
}


def _texto(animal, campo):
    valor = getattr(animal, campo)
    if campo == 'sexo':
        return animal.get_sexo_display()
    if campo == 'fecha_nacimiento':
        return valor.strftime('%d/%m/%Y') if valor else ''
    return '' if valor is None else str(valor)


class AnimalNuevoSerializer(serializers.Serializer):
    arete_1 = serializers.CharField(max_length=10, required=False, allow_blank=True, default='')
    arete_2 = serializers.CharField(max_length=10, required=False, allow_blank=True, default='')
    arete_3 = serializers.CharField(max_length=10, required=False, allow_blank=True, default='')
    especie_id = serializers.PrimaryKeyRelatedField(source='especie', queryset=Especie.objects.filter(activo=True))
    categoria_id = serializers.PrimaryKeyRelatedField(
        source='categoria', queryset=Categoria.objects.filter(activo=True),
    )
    sexo = serializers.ChoiceField(choices=SEXOS)
    raza = serializers.CharField(max_length=60, required=False, allow_blank=True, default='')
    fecha_nacimiento = serializers.DateField(required=False, allow_null=True, default=None)
    notas = serializers.CharField(required=False, allow_blank=True, default='')
    cantidad = serializers.IntegerField(min_value=1, max_value=500, required=False, default=1)

    def validate(self, attrs):
        if attrs['cantidad'] > 1 and any(attrs[c].strip() for c in ('arete_1', 'arete_2', 'arete_3')):
            raise serializers.ValidationError({'cantidad': 'Con arete se registra uno por renglón.'})
        return attrs


class MovimientoSerializer(serializers.ModelSerializer):
    """Alta (animales nuevos), baja o traspaso (animal_ids). Al editar solo cambian fecha, contraparte, importe y notas."""
    tipo = serializers.ChoiceField(choices=TIPOS)
    motivo = serializers.ChoiceField(choices=MOTIVOS, required=False, allow_blank=True, default='')
    rancho_id = serializers.PrimaryKeyRelatedField(source='rancho', queryset=Rancho.objects.all())
    rancho_destino_id = serializers.PrimaryKeyRelatedField(
        source='rancho_destino', queryset=Rancho.objects.all(), required=False, allow_null=True, default=None,
    )
    animales = AnimalNuevoSerializer(many=True, required=False, write_only=True)
    animal_ids = serializers.ListField(child=serializers.IntegerField(), required=False, write_only=True)

    class Meta:
        model = Movimiento
        fields = [
            'id', 'tipo', 'motivo', 'fecha', 'rancho_id', 'rancho_destino_id', 'contraparte', 'importe',
            'notas', 'animales', 'animal_ids',
        ]

    def to_representation(self, movimiento):
        # Las vistas traen prefetch de lineas__animal__categoria/especie.
        animales = [linea.animal for linea in movimiento.lineas.all()]
        datos = {
            'id': movimiento.pk,
            'folio': movimiento.folio,
            'tipo': movimiento.tipo,
            'tipo_nombre': movimiento.get_tipo_display(),
            'motivo': movimiento.motivo,
            'motivo_nombre': movimiento.get_motivo_display() if movimiento.motivo else '',
            'fecha': movimiento.fecha.isoformat(),
            'rancho': _ref(movimiento.rancho),
            'rancho_destino': _ref(movimiento.rancho_destino),
            'contraparte': movimiento.contraparte,
            'importe': None if movimiento.importe is None else f'{movimiento.importe:.2f}',
            'notas': movimiento.notas,
            'cabezas': len(animales),
            'resumen': ', '.join(f'{n} {c}' for c, n in Counter(a.categoria.nombre for a in animales).items()),
            'usuario': UsuarioResponsableSerializer(movimiento.usuario).data,
            'creado': movimiento.creado,
            'editado_por': UsuarioResponsableSerializer(movimiento.editado_por).data if movimiento.editado_por else None,
            'editado_en': movimiento.editado_en,
            'cancelado': movimiento.cancelado,
            'motivo_cancelacion': movimiento.motivo_cancelacion,
            'usuario_cancelacion': (UsuarioResponsableSerializer(movimiento.usuario_cancelacion).data
                                    if movimiento.usuario_cancelacion else None),
            'cancelado_en': movimiento.cancelado_en,
        }
        if self.context.get('detalle'):
            datos['animales'] = [{
                'id': a.pk, 'folio': a.folio, 'arete': a.arete, 'identificacion': a.identificacion,
                'especie': a.especie.nombre, 'categoria': a.categoria.nombre, 'sexo': a.sexo, 'raza': a.raza,
            } for a in animales]
        return datos


class EditarMovimientoSerializer(serializers.Serializer):
    fecha = serializers.DateField(required=False)
    contraparte = serializers.CharField(max_length=150, required=False, allow_blank=True)
    importe = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False, allow_null=True)
    notas = serializers.CharField(required=False, allow_blank=True)


class CancelarSerializer(serializers.Serializer):
    motivo = serializers.CharField(max_length=255)
