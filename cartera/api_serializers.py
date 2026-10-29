from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from inventario.models import Cliente, Empresa
from treasury.models import MovimientoTesoreria

from .models import AjusteCliente, ClienteCartera, PagoCliente, SaldoInicialCliente, VerificacionVenta
from .saldos import fecha_corte
from .ventas import empresa_de, estado, importe, producto_de

CODIGO_PESOS = 'MXN'


def _usuario_sesion(serializer):
    """El usuario que queda registrado siempre es el de la sesión, nunca un dato del cliente."""
    return serializer.context['request'].user


def _texto(value):
    return ' '.join((value or '').split())


class UsuarioSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()


class EmpresaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Empresa
        fields = ['id', 'nombre']


class ClienteCarteraSerializer(serializers.ModelSerializer):
    """Cliente (catálogo de Inventario) con sus datos de cobranza."""
    celular = serializers.CharField(source='cartera.celular', required=False, allow_blank=True, max_length=120)
    pagare = serializers.BooleanField(source='cartera.pagare', required=False)
    notas = serializers.CharField(source='cartera.notas', required=False, allow_blank=True)

    class Meta:
        model = Cliente
        fields = ['id', 'nombre', 'activo', 'celular', 'pagare', 'notas']

    def validate_nombre(self, value):
        value = _texto(value).upper()
        if not value:
            raise serializers.ValidationError('El nombre es obligatorio.')
        repetido = Cliente.objects.filter(nombre__iexact=value)
        if self.instance:
            repetido = repetido.exclude(pk=self.instance.pk)
        if repetido.exists():
            raise serializers.ValidationError('Ya existe un cliente con ese nombre.')
        return value

    def to_representation(self, instance):
        # Un cliente dado de alta en Inventario todavía no tiene datos de cobranza.
        if not hasattr(instance, 'cartera'):
            instance.cartera = ClienteCartera(cliente=instance)
        return super().to_representation(instance)

    def _guardar_cartera(self, cliente, datos):
        perfil, _ = ClienteCartera.objects.get_or_create(cliente=cliente)
        for campo, valor in datos.items():
            setattr(perfil, campo, _texto(valor) if campo == 'celular' else valor)
        perfil.save()
        cliente.cartera = perfil

    def create(self, validated_data):
        datos_cartera = validated_data.pop('cartera', {})
        with transaction.atomic():
            cliente = Cliente.objects.create(creado_por=_usuario_sesion(self), **validated_data)
            self._guardar_cartera(cliente, datos_cartera)
        return cliente

    def update(self, instance, validated_data):
        datos_cartera = validated_data.pop('cartera', {})
        with transaction.atomic():
            for campo, valor in validated_data.items():
                setattr(instance, campo, valor)
            instance.save()
            self._guardar_cartera(instance, datos_cartera)
        return instance


class SaldoInicialSerializer(serializers.ModelSerializer):
    cliente = serializers.CharField(source='cliente.nombre', read_only=True)
    empresa = serializers.CharField(source='empresa.nombre', read_only=True)

    class Meta:
        model = SaldoInicialCliente
        fields = ['id', 'cliente_id', 'cliente', 'empresa_id', 'empresa', 'monto', 'fecha']


class MovimientoCarteraSerializer(serializers.ModelSerializer):
    """Lo común de pagos y ajustes (cliente, empresa, fecha, rastro y cancelación)."""
    cliente = serializers.SerializerMethodField()
    cliente_id = serializers.PrimaryKeyRelatedField(source='cliente', queryset=Cliente.objects.all(), write_only=True)
    empresa = EmpresaSerializer(read_only=True)
    empresa_id = serializers.PrimaryKeyRelatedField(source='empresa', queryset=Empresa.objects.all(), write_only=True)
    usuario = UsuarioSerializer(read_only=True)
    editado_por = UsuarioSerializer(read_only=True)
    usuario_cancelacion = UsuarioSerializer(read_only=True)

    COMUNES = [
        'id', 'cliente', 'cliente_id', 'empresa', 'empresa_id', 'fecha', 'monto', 'notas',
        'usuario', 'creado', 'editado_por', 'editado_en',
        'cancelado', 'fecha_cancelacion', 'usuario_cancelacion', 'motivo_cancelacion',
    ]
    SOLO_LECTURA = [
        'creado', 'editado_en', 'cancelado', 'fecha_cancelacion', 'motivo_cancelacion',
    ]

    def get_cliente(self, obj):
        if obj.cliente_id is None:
            return None
        return {'id': obj.cliente_id, 'nombre': obj.cliente.nombre}

    def validate_monto(self, value):
        if value <= 0:
            raise serializers.ValidationError('El monto debe ser mayor que cero.')
        return value

    def validate_fecha(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError('La fecha no puede ser futura.')
        # Lo anterior al corte ya está en el saldo inicial: capturarlo lo contaría dos veces.
        corte = fecha_corte()
        if corte and value <= corte:
            siguiente = corte + timedelta(days=1)
            raise serializers.ValidationError(
                f'Los saldos iniciales son al cierre del {corte:%d/%m/%Y}: '
                f'captura movimientos del {siguiente:%d/%m/%Y} en adelante.'
            )
        return value

    def create(self, validated_data):
        validated_data['usuario'] = _usuario_sesion(self)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if instance.cancelado:
            raise serializers.ValidationError('No se puede editar un movimiento cancelado.')
        if any(getattr(instance, campo) != valor for campo, valor in validated_data.items()):
            validated_data['editado_por'] = _usuario_sesion(self)
            validated_data['editado_en'] = timezone.now()
        return super().update(instance, validated_data)


class PagoClienteSerializer(MovimientoCarteraSerializer):
    tipo_display = serializers.CharField(source='get_tipo_display', read_only=True)
    # Sin cliente = pago por identificar: no mueve ningún saldo hasta asignarlo.
    cliente_id = serializers.PrimaryKeyRelatedField(
        source='cliente', queryset=Cliente.objects.all(), write_only=True, allow_null=True, required=False,
    )
    por_identificar = serializers.BooleanField(read_only=True)
    identificado_por = UsuarioSerializer(read_only=True)
    ingreso_tesoreria = serializers.SerializerMethodField()
    ingreso_tesoreria_id = serializers.PrimaryKeyRelatedField(
        source='ingreso_tesoreria', write_only=True, required=False, allow_null=True,
        queryset=MovimientoTesoreria.objects.filter(tipo=MovimientoTesoreria.INGRESO),
    )
    # Con ingreso de Tesorería, fecha y monto salen de él.
    fecha = serializers.DateField(required=False)
    monto = serializers.DecimalField(max_digits=18, decimal_places=2, required=False)

    class Meta:
        model = PagoCliente
        # Sin el validador automático de DRF (mensaje técnico): la unicidad se valida en validate().
        validators = []
        fields = MovimientoCarteraSerializer.COMUNES + [
            'tipo', 'tipo_display', 'referencia', 'ingreso_tesoreria', 'ingreso_tesoreria_id',
            'por_identificar', 'identificado_por', 'identificado_en',
        ]
        read_only_fields = MovimientoCarteraSerializer.SOLO_LECTURA

    def get_ingreso_tesoreria(self, obj):
        ingreso = obj.ingreso_tesoreria
        if ingreso is None:
            return None
        return {
            'id': ingreso.id, 'folio': ingreso.folio, 'fecha': ingreso.fecha, 'beneficiario': ingreso.beneficiario,
            'concepto': ingreso.concepto, 'cancelado': ingreso.cancelado,
        }

    def validate(self, data):
        instancia = self.instance
        ingreso = data.get('ingreso_tesoreria', instancia.ingreso_tesoreria if instancia else None)
        if ingreso is not None:
            if ingreso.cancelado:
                raise serializers.ValidationError({'ingreso_tesoreria_id': 'El ingreso de Tesorería está cancelado.'})
            lineas = list(ingreso.divisas.select_related('divisa'))
            if len(lineas) != 1 or lineas[0].divisa.codigo != CODIGO_PESOS:
                raise serializers.ValidationError(
                    {'ingreso_tesoreria_id': 'Solo se ligan ingresos de Tesorería en pesos (MXN) de una sola divisa.'}
                )
            usado = PagoCliente.objects.filter(ingreso_tesoreria=ingreso, cancelado=False)
            if instancia:
                usado = usado.exclude(pk=instancia.pk)
            if usado.exists():
                raise serializers.ValidationError(
                    {'ingreso_tesoreria_id': f'El ingreso {ingreso.folio} ya está registrado en otro pago.'}
                )
            data['fecha'] = self.validate_fecha(ingreso.fecha)
            data['monto'] = lineas[0].cantidad
        else:
            # El efectivo que entra a Tesorería ya está registrado allá: no se recaptura.
            tipo = data.get('tipo', getattr(instancia, 'tipo', None))
            if tipo == PagoCliente.EFECTIVO_TESORERIA:
                raise serializers.ValidationError(
                    {'ingreso_tesoreria_id': 'Un pago en efectivo a Tesorería se toma de su ingreso de Tesorería.'}
                )
            for campo in ('fecha', 'monto'):
                if data.get(campo, getattr(instancia, campo, None)) is None:
                    raise serializers.ValidationError({campo: 'Este campo es obligatorio.'})
        return data

    def validate_referencia(self, value):
        return _texto(value)


class IdentificarPagoSerializer(serializers.Serializer):
    cliente_id = serializers.PrimaryKeyRelatedField(source='cliente', queryset=Cliente.objects.all())


class AjusteClienteSerializer(MovimientoCarteraSerializer):
    naturaleza_display = serializers.CharField(source='get_naturaleza_display', read_only=True)
    es_traspaso = serializers.BooleanField(read_only=True)
    contraparte = serializers.SerializerMethodField()

    class Meta:
        model = AjusteCliente
        fields = MovimientoCarteraSerializer.COMUNES + [
            'naturaleza', 'naturaleza_display', 'concepto', 'es_traspaso', 'contraparte',
        ]
        read_only_fields = MovimientoCarteraSerializer.SOLO_LECTURA

    def get_contraparte(self, obj):
        otro = obj.contraparte
        if otro is None:
            return None
        return {'id': otro.id, 'empresa': otro.empresa.nombre, 'naturaleza': otro.naturaleza}

    def validate_concepto(self, value):
        value = _texto(value)
        if not value:
            raise serializers.ValidationError('El concepto es obligatorio.')
        return value

    def update(self, instance, validated_data):
        # Un traspaso son dos ajustes: se corrige cancelándolo y capturándolo de nuevo.
        if instance.es_traspaso:
            raise serializers.ValidationError('Un traspaso no se edita: cancélalo y regístralo de nuevo.')
        return super().update(instance, validated_data)


class TraspasoSerializer(serializers.Serializer):
    """Saldo que pasa de una empresa a otra: abono en el origen y cargo en el destino."""
    cliente_id = serializers.PrimaryKeyRelatedField(source='cliente', queryset=Cliente.objects.all())
    origen_id = serializers.PrimaryKeyRelatedField(source='origen', queryset=Empresa.objects.all())
    destino_id = serializers.PrimaryKeyRelatedField(source='destino', queryset=Empresa.objects.all())
    fecha = serializers.DateField()
    monto = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal('0.01'))
    concepto = serializers.CharField(max_length=255)
    notas = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_fecha(self, value):
        return MovimientoCarteraSerializer.validate_fecha(self, value)

    def validate_concepto(self, value):
        value = _texto(value)
        if not value:
            raise serializers.ValidationError('El concepto es obligatorio.')
        return value

    def validate(self, data):
        if data['origen'] == data['destino']:
            raise serializers.ValidationError({'destino_id': 'El destino debe ser otra empresa.'})
        return data

    def save(self):
        datos = self.validated_data
        comunes = {
            'cliente': datos['cliente'], 'fecha': datos['fecha'], 'monto': datos['monto'],
            'concepto': datos['concepto'], 'notas': datos['notas'], 'usuario': _usuario_sesion(self),
        }
        with transaction.atomic():
            abono = AjusteCliente.objects.create(empresa=datos['origen'], naturaleza=AjusteCliente.ABONO, **comunes)
            cargo = AjusteCliente.objects.create(
                empresa=datos['destino'], naturaleza=AjusteCliente.CARGO, contraparte=abono, **comunes,
            )
            abono.contraparte = cargo
            abono.save()
        return abono, cargo


class IngresoTesoreriaSerializer(serializers.ModelSerializer):
    """Ingreso de caja de Tesorería que Cartera puede tomar como pago en efectivo."""
    monto = serializers.SerializerMethodField()

    class Meta:
        model = MovimientoTesoreria
        fields = ['id', 'folio', 'fecha', 'beneficiario', 'concepto', 'monto']

    def get_monto(self, obj):
        return str(sum((linea.cantidad for linea in obj.divisas.all()), Decimal('0')))


# --- Ventas (salidas de Inventario) y su verificación --------------------------

def linea_venta(linea):
    """Línea de salida tal como la revisa Cartera, con su estado de verificación."""
    estado_linea, cambio = estado(linea)
    try:
        verificacion = linea.verificacion_cartera
    except VerificacionVenta.DoesNotExist:
        verificacion = None
    monto = importe(linea)
    return {
        'id': linea.id,
        'producto': producto_de(linea),
        'lote': linea.entrada_detalle.lote_general.codigo if linea.entrada_detalle.lote_general_id else '',
        'camara': linea.camara.nombre if linea.camara_id else '',
        'cajas': linea.cajas,
        'kilos': str(linea.total_kilos),
        'precio_kilo': str(linea.precio_x_kilo) if linea.precio_x_kilo is not None else None,
        'importe': str(monto) if monto is not None else None,
        'estado': estado_linea,
        # Había revisión, pero Inventario corrigió la línea después: hay que revisarla de nuevo.
        'corregida_por_inventario': cambio,
        'comentario': verificacion.comentario if verificacion and not cambio else '',
        'verificado_por': verificacion.usuario.username if verificacion and not cambio else None,
        'verificado_en': verificacion.fecha if verificacion and not cambio else None,
    }


def ventas_agrupadas(lineas):
    """Líneas agrupadas por salida (y empresa del lote), en orden de fecha."""
    salidas = {}
    for linea in lineas:
        empresa = empresa_de(linea)
        clave = (linea.salida_id, empresa.id)
        if clave not in salidas:
            salidas[clave] = {
                'salida_id': linea.salida_id, 'folio_salida': linea.salida.folio_de_salida,
                'nota_salida': linea.salida.notas, 'fecha': linea.salida.fecha,
                'empresa': {'id': empresa.id, 'nombre': empresa.nombre}, 'lineas': [], 'total': Decimal('0'),
            }
        datos = linea_venta(linea)
        salidas[clave]['lineas'].append(datos)
        salidas[clave]['total'] += Decimal(datos['importe'] or '0')
    resultado = list(salidas.values())
    for venta in resultado:
        venta['total'] = str(venta['total'])
    return resultado


class VerificarVentaSerializer(serializers.Serializer):
    """Marca líneas de venta como verificadas, con diferencia (comentario obligatorio) o de nuevo pendientes."""
    lineas = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    estado = serializers.ChoiceField(choices=['verificada', 'diferencia', 'pendiente'])
    comentario = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, data):
        data['comentario'] = _texto(data['comentario'])
        if data['estado'] == 'diferencia' and not data['comentario']:
            raise serializers.ValidationError({'comentario': 'Describe la diferencia para que Inventario la corrija.'})
        return data
