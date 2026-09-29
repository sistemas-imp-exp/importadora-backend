from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password as validar_password_django
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from security.models import Area, UsuarioArea

from .models import Perfil

User = get_user_model()


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = authenticate(
            username=attrs["username"],
            password=attrs["password"],
        )

        if not user:
            raise serializers.ValidationError("Usuario o contraseña incorrectos.")

        if not user.is_active:
            raise serializers.ValidationError("Usuario inactivo.")

        attrs["user"] = user
        return attrs


def _url_foto(usuario, request):
    try:
        foto = usuario.perfil.foto
    except Perfil.DoesNotExist:
        return None
    if not foto:
        return None
    return request.build_absolute_uri(foto.url) if request else foto.url


class UsuarioMeSerializer(serializers.ModelSerializer):
    areas = serializers.SerializerMethodField()
    areas_solo_lectura = serializers.SerializerMethodField()
    foto = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'username', 'first_name', 'last_name', 'email',
            'is_superuser', 'areas', 'areas_solo_lectura', 'foto',
        ]

    def get_areas(self, obj):
        # Solo activas: mismo criterio que security.permissions.tiene_area, para
        # que el menú del frontend no ofrezca módulos que el backend va a negar.
        return [ua.area.codigo for ua in obj.areas.select_related('area').filter(area__activo=True)]

    def get_areas_solo_lectura(self, obj):
        """Subconjunto de `areas` en el que el usuario solo consulta y descarga."""
        return [
            ua.area.codigo
            for ua in obj.areas.select_related('area').filter(area__activo=True, solo_lectura=True)
        ]

    def get_foto(self, obj):
        return _url_foto(obj, self.context.get('request'))


class ActualizarPerfilSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']


class CambiarPasswordSerializer(serializers.Serializer):
    password_actual = serializers.CharField(write_only=True)
    password_nueva = serializers.CharField(write_only=True)
    password_nueva2 = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = self.context['request'].user

        if not user.check_password(attrs['password_actual']):
            raise serializers.ValidationError({'password_actual': 'La contraseña actual es incorrecta.'})

        if attrs['password_nueva'] != attrs['password_nueva2']:
            raise serializers.ValidationError({'password_nueva2': 'Las contraseñas nuevas no coinciden.'})

        try:
            validar_password_django(attrs['password_nueva'], user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password_nueva': list(exc.messages)})

        return attrs

    def save(self):
        user = self.context['request'].user
        user.set_password(self.validated_data['password_nueva'])
        user.save(update_fields=['password'])
        return user


class FotoPerfilSerializer(serializers.ModelSerializer):
    class Meta:
        model = Perfil
        fields = ['foto']
        extra_kwargs = {'foto': {'required': True}}

    def to_representation(self, instance):
        return {'foto': _url_foto(instance.usuario, self.context.get('request'))}

    def update(self, instance, validated_data):
        if instance.foto:
            instance.foto.delete(save=False)
        instance.foto = validated_data['foto']
        instance.save()
        return instance


class UsuarioCatalogoSerializer(serializers.ModelSerializer):
    foto = serializers.SerializerMethodField()
    areas = serializers.ListField(child=serializers.CharField(), required=False, write_only=True)
    # Subconjunto de `areas` que se asigna en solo lectura (consulta y descarga).
    areas_solo_lectura = serializers.ListField(child=serializers.CharField(), required=False, write_only=True)
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = [
            'id', 'username', 'first_name', 'last_name', 'email',
            'is_active', 'is_superuser', 'foto', 'areas', 'areas_solo_lectura', 'password',
        ]

    def get_foto(self, obj):
        return _url_foto(obj, self.context.get('request'))

    def validate_username(self, value):
        qs = User.objects.filter(username=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Ya existe un usuario con ese nombre de usuario.")
        return value

    def validate_password(self, value):
        if value:
            try:
                validar_password_django(value)
            except DjangoValidationError as exc:
                raise serializers.ValidationError(list(exc.messages))
        return value

    def validate_areas(self, value):
        codigos_validos = set(Area.objects.filter(activo=True).values_list('codigo', flat=True))
        invalidos = set(value) - codigos_validos
        if invalidos:
            raise serializers.ValidationError(f"Área(s) inválida(s): {', '.join(sorted(invalidos))}.")
        return value

    def validate(self, attrs):
        solo_lectura = attrs.get('areas_solo_lectura')
        if solo_lectura:
            asignadas = attrs.get('areas')
            if asignadas is None and self.instance is not None:
                asignadas = list(self.instance.areas.values_list('area__codigo', flat=True))
            sobrantes = set(solo_lectura) - set(asignadas or [])
            if sobrantes:
                raise serializers.ValidationError({
                    'areas_solo_lectura': "Solo lectura en área(s) no asignada(s): " + ", ".join(sorted(sobrantes)) + ".",
                })

        if self.instance is None and not attrs.get('password'):
            raise serializers.ValidationError({'password': 'La contraseña es obligatoria al crear un usuario.'})

        request = self.context.get('request')
        if self.instance is not None and request and request.user.pk == self.instance.pk:
            if attrs.get('is_active') is False:
                raise serializers.ValidationError({'is_active': 'No puedes desactivar tu propia cuenta.'})
            if attrs.get('is_superuser') is False:
                raise serializers.ValidationError({'is_superuser': 'No puedes quitarte a ti mismo el rol de superusuario.'})

        return attrs

    def create(self, validated_data):
        areas = validated_data.pop('areas', [])
        solo_lectura = validated_data.pop('areas_solo_lectura', [])
        password = validated_data.pop('password')

        with transaction.atomic():
            user = User(**validated_data)
            user.set_password(password)
            user.save()
            self._sincronizar_areas(user, areas, solo_lectura)

        return user

    def update(self, instance, validated_data):
        areas = validated_data.pop('areas', None)
        solo_lectura = validated_data.pop('areas_solo_lectura', None)
        password = validated_data.pop('password', None)

        with transaction.atomic():
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            if password:
                instance.set_password(password)
            instance.save()

            if areas is not None or solo_lectura is not None:
                if areas is None:
                    areas = list(instance.areas.values_list('area__codigo', flat=True))
                self._sincronizar_areas(instance, areas, solo_lectura or [])

        return instance

    def _sincronizar_areas(self, usuario, codigos, solo_lectura):
        UsuarioArea.objects.filter(usuario=usuario).exclude(area__codigo__in=codigos).delete()

        existentes = set(
            UsuarioArea.objects.filter(usuario=usuario).values_list('area__codigo', flat=True)
        )
        nuevas = [
            UsuarioArea(usuario=usuario, area=area)
            for area in Area.objects.filter(codigo__in=codigos)
            if area.codigo not in existentes
        ]
        UsuarioArea.objects.bulk_create(nuevas)
        asignaciones = UsuarioArea.objects.filter(usuario=usuario)
        asignaciones.filter(area__codigo__in=solo_lectura).update(solo_lectura=True)
        asignaciones.exclude(area__codigo__in=solo_lectura).update(solo_lectura=False)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        asignaciones = list(
            UsuarioArea.objects.filter(usuario=instance).values_list('area__codigo', 'solo_lectura')
        )
        data['areas'] = [codigo for codigo, _ in asignaciones]
        data['areas_solo_lectura'] = [codigo for codigo, solo in asignaciones if solo]
        return data
