from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from .models import (
    ArqueoCaja,
    ArqueoConteo,
    ArqueoDivisa,
    Banco,
    ConfiguracionFolio,
    Denominacion,
    Deuda,
    Deudor,
    Divisa,
    EdicionMovimiento,
    Empleado,
    MovimientoArchivo,
    MovimientoDivisa,
    MovimientoTesoreria,
    NominaDetalle,
    NominaSemanal,
    Puesto,
    Rancho,
    SaldoInicial,
)
from .saldos import resumen_dia

User = get_user_model()


class UsuarioResponsableSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'last_name']


class DivisaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Divisa
        fields = ['id', 'codigo', 'nombre', 'simbolo', 'activa']


class SaldoInicialEntradaSerializer(serializers.Serializer):
    divisa_id = serializers.PrimaryKeyRelatedField(source='divisa', queryset=Divisa.objects.all())
    monto = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal('0'))


class GuardarSaldosInicialesSerializer(serializers.Serializer):
    saldos = SaldoInicialEntradaSerializer(many=True)

    def validate_saldos(self, value):
        ids = [s['divisa'].id for s in value]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError('Cada divisa debe aparecer una sola vez.')
        return value

    def save(self):
        usuario = _usuario_sesion(self)
        with transaction.atomic():
            for saldo in self.validated_data['saldos']:
                SaldoInicial.objects.update_or_create(
                    divisa=saldo['divisa'], defaults={'monto': saldo['monto'], 'editado_por': usuario},
                )


class MovimientoDivisaSerializer(serializers.ModelSerializer):
    divisa = DivisaSerializer(read_only=True)
    divisa_id = serializers.PrimaryKeyRelatedField(
        source='divisa', queryset=Divisa.objects.filter(activa=True), write_only=True
    )  # para POST/PUT: solo el id

    class Meta:
        model = MovimientoDivisa
        fields = ['id', 'divisa', 'divisa_id', 'cantidad']


def _avanzar_folio(tipo, folio_valor):
    """Adelanta el consecutivo sugerido de folio si el folio usado (editado o no) lo supera."""
    digitos = ''.join(ch for ch in folio_valor if ch.isdigit())
    if not digitos:
        return
    numero = int(digitos)
    config, _ = ConfiguracionFolio.objects.get_or_create(tipo=tipo, defaults={'siguiente_folio': 1})
    if numero >= config.siguiente_folio:
        config.siguiente_folio = numero + 1
        config.save()


CAMPOS_HISTORIAL = (
    ('fecha', 'Fecha'),
    ('folio', 'Folio'),
    ('tipo', 'Tipo'),
    ('autorizo', 'Autorizó'),
    ('beneficiario', 'Beneficiario'),
    ('concepto', 'Concepto'),
    ('deuda', 'Abono a deuda'),
)


def _foto_movimiento(movimiento, lineas):
    """Valores legibles del movimiento para comparar antes y después de una edición."""
    deuda = movimiento.deuda
    foto = {
        'fecha': movimiento.fecha.strftime('%d/%m/%Y'),
        'folio': movimiento.folio,
        'tipo': movimiento.get_tipo_display(),
        'autorizo': movimiento.autorizo,
        'beneficiario': movimiento.beneficiario,
        'concepto': movimiento.concepto,
        'deuda': f"{deuda.deudor.nombre}: {deuda.concepto}" if deuda else '',
    }
    cantidades = {linea.divisa.codigo: f"{linea.divisa.simbolo}{linea.cantidad:,.2f}" for linea in lineas}
    return foto, cantidades


def _diferencias(antes, despues):
    (campos_antes, cantidades_antes), (campos_despues, cantidades_despues) = antes, despues
    cambios = [
        {'campo': etiqueta, 'antes': campos_antes[campo], 'despues': campos_despues[campo]}
        for campo, etiqueta in CAMPOS_HISTORIAL
        if campos_antes[campo] != campos_despues[campo]
    ]
    for codigo in sorted(set(cantidades_antes) | set(cantidades_despues)):
        if cantidades_antes.get(codigo) != cantidades_despues.get(codigo):
            cambios.append({
                'campo': f'Cantidad {codigo}',
                'antes': cantidades_antes.get(codigo, ''),
                'despues': cantidades_despues.get(codigo, ''),
            })
    return cambios


def _usuario_sesion(serializer):
    """El usuario que queda registrado siempre es el de la sesión, nunca un dato del cliente."""
    return serializer.context['request'].user


class MovimientoTesoreriaSerializer(serializers.ModelSerializer):
    # Declarado explícito para que DRF no agregue su UniqueValidator automático
    # (mensaje técnico); la unicidad la valida validate_folio() más abajo.
    folio = serializers.CharField(max_length=20)
    divisas = MovimientoDivisaSerializer(many=True)
    usuario = UsuarioResponsableSerializer(read_only=True)
    editado_por = UsuarioResponsableSerializer(read_only=True)
    usuario_cancelacion = UsuarioResponsableSerializer(read_only=True)
    archivos_count = serializers.SerializerMethodField()
    deuda = serializers.SerializerMethodField()
    # Abono a deuda (opcional): solo ingresos, ver _validar_abono().
    deuda_id = serializers.PrimaryKeyRelatedField(
        source='deuda', queryset=Deuda.objects.all(), write_only=True, required=False, allow_null=True,
    )

    class Meta:
        model = MovimientoTesoreria
        fields = [
            'id', 'fecha', 'folio', 'tipo', 'autorizo', 'beneficiario', 'concepto',
            'creado', 'modificado', 'divisas', 'usuario',
            'editado', 'editado_por', 'editado_en',
            'cancelado', 'fecha_cancelacion', 'motivo_cancelacion', 'usuario_cancelacion',
            'archivos_count', 'deuda', 'deuda_id',
        ]
        read_only_fields = [
            'id', 'creado', 'modificado', 'archivos_count', 'usuario', 'editado', 'editado_por', 'editado_en',
            'cancelado', 'fecha_cancelacion', 'motivo_cancelacion', 'usuario_cancelacion',
        ]

    def get_archivos_count(self, obj):
        return obj.archivos.filter(eliminado=False).count()

    def get_deuda(self, obj):
        if obj.deuda_id is None:
            return None
        deuda = obj.deuda
        return {
            'id': deuda.id,
            'concepto': deuda.concepto,
            'deudor': {'id': deuda.deudor_id, 'nombre': deuda.deudor.nombre},
        }

    def validate_folio(self, value):
        consulta = MovimientoTesoreria.objects.filter(folio=value)
        if self.instance:
            consulta = consulta.exclude(pk=self.instance.pk)
        if consulta.exists():
            raise serializers.ValidationError('Ya existe un movimiento registrado con este folio.')
        return value

    def validate(self, data):
        divisas = data.get('divisas')
        if not divisas:
            raise serializers.ValidationError({'divisas': 'Debe incluir al menos una divisa con cantidad.'})
        for d in divisas:
            if Decimal(d.get('cantidad') or 0) <= 0:
                raise serializers.ValidationError({'divisas': 'Las cantidades deben ser mayores que cero.'})
        return data

    def _validar_abono(self, validated_data, instance=None):
        """
        Un abono a deuda es un ingreso en una sola divisa, la de la deuda, por
        no más del saldo pendiente. Se llama dentro de la transacción y con la
        deuda bloqueada, para que dos abonos simultáneos no pasen del saldo.
        """
        deuda = validated_data.get('deuda', instance.deuda if instance else None)
        if deuda is None:
            return
        deuda = Deuda.objects.select_for_update().get(pk=deuda.pk)
        tipo = validated_data.get('tipo', instance.tipo if instance else None)
        fecha = validated_data.get('fecha', instance.fecha if instance else None)
        divisas = validated_data['divisas']
        if tipo != MovimientoTesoreria.INGRESO:
            raise serializers.ValidationError({'deuda_id': 'Solo un ingreso puede abonar a una deuda.'})
        if deuda.cancelada:
            raise serializers.ValidationError({'deuda_id': 'La deuda está cancelada.'})
        if len(divisas) != 1 or divisas[0]['divisa'].id != deuda.divisa_id:
            raise serializers.ValidationError(
                {'divisas': f'El abono debe ser en una sola divisa: {deuda.divisa.codigo}, la de la deuda.'}
            )
        if fecha and fecha < deuda.fecha:
            raise serializers.ValidationError({'fecha': 'El abono no puede ser anterior a la fecha de la deuda.'})
        pendiente = deuda.monto - deuda.calcular_abonado(excluir_movimiento=instance)
        if divisas[0]['cantidad'] > pendiente:
            raise serializers.ValidationError(
                {'divisas': f'El abono excede el saldo pendiente de la deuda ({deuda.divisa.simbolo}{pendiente:,.2f}).'}
            )

    # Sin validación de saldo suficiente: al pasar hojas atrasadas es normal
    # capturar un egreso antes que el ingreso del mismo día. Los días que
    # quedan en negativo los marca la Caja diaria (treasury/saldos.py).
    def create(self, validated_data):
        validated_data['usuario'] = _usuario_sesion(self)
        with transaction.atomic():
            self._validar_abono(validated_data)
            divisas_data = validated_data.pop('divisas')
            movimiento = MovimientoTesoreria.objects.create(**validated_data)
            for item in divisas_data:
                MovimientoDivisa.objects.create(movimiento=movimiento, divisa=item['divisa'], cantidad=item['cantidad'])
            _avanzar_folio(movimiento.tipo, movimiento.folio)
        return movimiento

    def update(self, instance, validated_data):
        if instance.cancelado:
            raise serializers.ValidationError('No se puede editar un movimiento cancelado.')

        with transaction.atomic():
            self._validar_abono(validated_data, instance)
            antes = _foto_movimiento(instance, list(instance.divisas.select_related('divisa')))
            divisas_data = validated_data.pop('divisas')
            hubo_cambio = False
            for campo in ['fecha', 'folio', 'tipo', 'autorizo', 'beneficiario', 'concepto', 'deuda']:
                if campo in validated_data:
                    if getattr(instance, campo) != validated_data[campo]:
                        hubo_cambio = True
                    setattr(instance, campo, validated_data[campo])

            lineas_actuales = {linea.divisa_id: linea for linea in instance.divisas.all()}
            ids_nuevos = set()
            for item in divisas_data:
                divisa, cantidad = item['divisa'], item['cantidad']
                ids_nuevos.add(divisa.id)
                linea = lineas_actuales.get(divisa.id)
                if linea:
                    if linea.cantidad != cantidad:
                        hubo_cambio = True
                        linea.cantidad = cantidad
                        linea.save()
                else:
                    hubo_cambio = True
                    MovimientoDivisa.objects.create(movimiento=instance, divisa=divisa, cantidad=cantidad)
            for divisa_id, linea in lineas_actuales.items():
                if divisa_id not in ids_nuevos:
                    hubo_cambio = True
                    linea.delete()

            if hubo_cambio:
                instance.editado = True
                instance.editado_por = _usuario_sesion(self)
                instance.editado_en = timezone.now()
            instance.save()
            cambios = _diferencias(antes, _foto_movimiento(instance, list(instance.divisas.select_related('divisa'))))
            if cambios:
                EdicionMovimiento.objects.create(
                    movimiento=instance, usuario=instance.editado_por, fecha=instance.editado_en, cambios=cambios,
                )
            _avanzar_folio(instance.tipo, instance.folio)
        return instance


class DenominacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Denominacion
        fields = ['id', 'divisa', 'valor', 'tipo', 'activa']


class ArqueoConteoSerializer(serializers.ModelSerializer):
    denominacion = DenominacionSerializer(read_only=True)
    denominacion_id = serializers.PrimaryKeyRelatedField(
        source='denominacion', queryset=Denominacion.objects.filter(activa=True), write_only=True
    )
    total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    class Meta:
        model = ArqueoConteo
        fields = ['id', 'denominacion', 'denominacion_id', 'piezas', 'total']


class ArqueoDivisaSerializer(serializers.ModelSerializer):
    divisa = DivisaSerializer(read_only=True)
    divisa_id = serializers.PrimaryKeyRelatedField(
        source='divisa', queryset=Divisa.objects.filter(activa=True), write_only=True
    )
    conteos = ArqueoConteoSerializer(many=True)
    estado = serializers.CharField(read_only=True)

    class Meta:
        model = ArqueoDivisa
        fields = [
            'id', 'divisa', 'divisa_id', 'saldo_inicial', 'resultado_esperado',
            'total_contado', 'diferencia', 'estado', 'conteos',
        ]
        read_only_fields = ['id', 'saldo_inicial', 'resultado_esperado', 'total_contado', 'diferencia']

    def validate_conteos(self, conteos):
        for item in conteos:
            if item['piezas'] < 0:
                raise serializers.ValidationError('El número de piezas no puede ser negativo.')
        return conteos


class ArqueoCajaSerializer(serializers.ModelSerializer):
    usuario = UsuarioResponsableSerializer(read_only=True)
    editado_por = UsuarioResponsableSerializer(read_only=True)
    divisas = ArqueoDivisaSerializer(many=True)
    leyenda_totales = serializers.CharField(read_only=True)

    class Meta:
        model = ArqueoCaja
        fields = [
            'id', 'fecha', 'hora_inicio', 'hora_termino', 'usuario', 'editado_por', 'editado_en',
            'observaciones', 'creado', 'modificado', 'divisas', 'leyenda_totales',
        ]
        read_only_fields = ['id', 'hora_termino', 'usuario', 'editado_por', 'editado_en', 'creado', 'modificado']

    def validate_fecha(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError('La fecha del arqueo no puede ser futura.')
        return value

    def validate(self, data):
        divisas_data = data.get('divisas')
        if not divisas_data:
            raise serializers.ValidationError({'divisas': 'Debe incluir el conteo de todas las divisas activas.'})

        divisa_ids_enviados = {item['divisa'].id for item in divisas_data}
        divisa_ids_activas = set(Divisa.objects.filter(activa=True).values_list('id', flat=True))
        faltantes = divisa_ids_activas - divisa_ids_enviados
        if faltantes:
            codigos = Divisa.objects.filter(id__in=faltantes).values_list('codigo', flat=True)
            raise serializers.ValidationError({'divisas': f"Falta el conteo de: {', '.join(codigos)}."})

        for item in divisas_data:
            divisa = item['divisa']
            for conteo in item['conteos']:
                if conteo['denominacion'].divisa_id != divisa.id:
                    raise serializers.ValidationError({
                        'divisas': f"La denominación {conteo['denominacion']} no corresponde a {divisa.codigo}."
                    })
        return data

    def _guardar_divisas(self, arqueo, divisas_data):
        # Foto del saldo calculado de ese día en el momento del conteo.
        filas = resumen_dia(arqueo.fecha)
        esperado = {f['divisa'].id: f for f in filas}
        previas = {linea.divisa_id: linea for linea in arqueo.divisas.all()}
        ids_enviados = set()

        for item in divisas_data:
            divisa = item['divisa']
            ids_enviados.add(divisa.id)
            arqueo_divisa = previas.get(divisa.id)
            if arqueo_divisa:
                arqueo_divisa.conteos.all().delete()
            else:
                arqueo_divisa = ArqueoDivisa(arqueo=arqueo, divisa=divisa)

            fila = esperado.get(divisa.id)
            arqueo_divisa.saldo_inicial = fila['saldo_inicial'] if fila else Decimal('0')
            arqueo_divisa.resultado_esperado = fila['saldo_final'] if fila else Decimal('0')
            arqueo_divisa.save()

            for conteo in item['conteos']:
                ArqueoConteo.objects.create(
                    arqueo_divisa=arqueo_divisa, denominacion=conteo['denominacion'], piezas=conteo['piezas'],
                )
            arqueo_divisa.recalcular_total()

        for divisa_id, arqueo_divisa in previas.items():
            if divisa_id not in ids_enviados:
                arqueo_divisa.delete()

    def create(self, validated_data):
        divisas_data = validated_data.pop('divisas')
        with transaction.atomic():
            arqueo = ArqueoCaja.objects.create(
                fecha=validated_data.get('fecha') or timezone.localdate(),
                hora_inicio=validated_data['hora_inicio'],
                hora_termino=timezone.now(),
                usuario=_usuario_sesion(self),
                observaciones=validated_data.get('observaciones', ''),
            )
            self._guardar_divisas(arqueo, divisas_data)
        return arqueo

    def update(self, instance, validated_data):
        divisas_data = validated_data.pop('divisas')
        with transaction.atomic():
            for campo in ('fecha', 'hora_inicio', 'observaciones'):
                if campo in validated_data:
                    setattr(instance, campo, validated_data[campo])
            instance.hora_termino = timezone.now()
            instance.editado_por = _usuario_sesion(self)
            instance.editado_en = timezone.now()
            instance.save()
            self._guardar_divisas(instance, divisas_data)
        return instance


class MovimientoArchivoSerializer(serializers.ModelSerializer):
    movimiento_id = serializers.PrimaryKeyRelatedField(
        source='movimiento', queryset=MovimientoTesoreria.objects.all(), write_only=True
    )
    subido_por = UsuarioResponsableSerializer(read_only=True)

    class Meta:
        model = MovimientoArchivo
        fields = [
            'id', 'movimiento_id', 'archivo', 'nombre_original', 'tipo_contenido',
            'tamano', 'subido_por', 'subido_en',
        ]
        read_only_fields = ['id', 'nombre_original', 'tipo_contenido', 'tamano', 'subido_por', 'subido_en']
        extra_kwargs = {'archivo': {'write_only': True}}

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        data['url'] = request.build_absolute_uri(instance.archivo.url) if request else instance.archivo.url
        data['movimiento'] = instance.movimiento_id
        return data

    def create(self, validated_data):
        archivo = validated_data['archivo']
        return MovimientoArchivo.objects.create(
            movimiento=validated_data['movimiento'],
            archivo=archivo,
            nombre_original=archivo.name,
            tipo_contenido=getattr(archivo, 'content_type', '') or '',
            tamano=archivo.size,
            subido_por=self.context['request'].user,
        )


class RanchoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Rancho
        fields = ['id', 'nombre', 'activo']


class PuestoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Puesto
        fields = ['id', 'nombre', 'activo']


class BancoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Banco
        fields = ['id', 'nombre', 'activo']


class EmpleadoSerializer(serializers.ModelSerializer):
    rancho = RanchoSerializer(read_only=True)
    rancho_id = serializers.PrimaryKeyRelatedField(
        source='rancho', queryset=Rancho.objects.filter(activo=True), write_only=True
    )
    puesto = PuestoSerializer(read_only=True)
    puesto_id = serializers.PrimaryKeyRelatedField(
        source='puesto', queryset=Puesto.objects.filter(activo=True), write_only=True
    )
    banco = BancoSerializer(read_only=True)
    banco_id = serializers.PrimaryKeyRelatedField(
        source='banco', queryset=Banco.objects.filter(activo=True),
        write_only=True, required=False, allow_null=True,
    )

    class Meta:
        model = Empleado
        fields = [
            'id', 'rancho', 'rancho_id', 'puesto', 'puesto_id',
            'nombre', 'salario_diario', 'numero_cuenta', 'banco', 'banco_id',
            'nombre_cuenta', 'activo',
        ]


class NominaDetalleSerializer(serializers.ModelSerializer):
    empleado = EmpleadoSerializer(read_only=True)
    empleado_id = serializers.PrimaryKeyRelatedField(
        source='empleado', queryset=Empleado.objects.filter(activo=True), write_only=True
    )
    total_bruto = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    total_neto = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = NominaDetalle
        fields = [
            'id', 'empleado', 'empleado_id', 'dias_trabajados', 'salario_diario',
            'descuento', 'total_bruto', 'total_neto',
        ]

    def validate_dias_trabajados(self, value):
        if not (Decimal('0') <= value <= Decimal('7')):
            raise serializers.ValidationError('Los días trabajados deben estar entre 0 y 7.')
        return value

    def validate_salario_diario(self, value):
        if value < 0:
            raise serializers.ValidationError('El salario diario no puede ser negativo.')
        return value

    def validate_descuento(self, value):
        if value < 0:
            raise serializers.ValidationError('El descuento no puede ser negativo.')
        return value


class NominaSemanalSerializer(serializers.ModelSerializer):
    creado_por = UsuarioResponsableSerializer(read_only=True)
    cerrada_por = UsuarioResponsableSerializer(read_only=True)
    detalles = NominaDetalleSerializer(many=True)
    total_bruto = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    total_neto = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    class Meta:
        model = NominaSemanal
        fields = [
            'id', 'fecha_inicio', 'fecha_fin', 'cerrada', 'fecha_cierre', 'cerrada_por',
            'creado_por', 'observaciones', 'creado', 'modificado', 'detalles',
            'total_bruto', 'total_neto',
        ]
        read_only_fields = ['id', 'cerrada', 'fecha_cierre', 'cerrada_por', 'creado_por', 'creado', 'modificado']

    def validate(self, data):
        fecha_inicio = data.get('fecha_inicio', getattr(self.instance, 'fecha_inicio', None))
        fecha_fin = data.get('fecha_fin', getattr(self.instance, 'fecha_fin', None))
        if fecha_inicio and fecha_fin and fecha_fin < fecha_inicio:
            raise serializers.ValidationError({'fecha_fin': 'No puede ser anterior a la fecha de inicio.'})

        for item in data.get('detalles') or []:
            if item['descuento'] > item['salario_diario'] * item['dias_trabajados']:
                raise serializers.ValidationError({
                    'detalles': f"El descuento de {item['empleado'].nombre} no puede ser mayor a su total bruto."
                })
        return data

    def _guardar_detalles(self, nomina, detalles_data):
        detalles_previos = {detalle.empleado_id: detalle for detalle in nomina.detalles.all()} if nomina.pk else {}
        ids_enviados = set()

        for item in detalles_data:
            empleado = item['empleado']
            ids_enviados.add(empleado.id)

            detalle = detalles_previos.get(empleado.id)
            if not detalle:
                detalle = NominaDetalle(nomina=nomina, empleado=empleado)

            detalle.dias_trabajados = item['dias_trabajados']
            detalle.descuento = item.get('descuento', 0)
            detalle.salario_diario = item['salario_diario']
            detalle.full_clean()
            detalle.save()

        for empleado_id, detalle in detalles_previos.items():
            if empleado_id not in ids_enviados:
                detalle.delete()

    def create(self, validated_data):
        detalles_data = validated_data.pop('detalles')
        nomina = NominaSemanal.objects.create(
            fecha_inicio=validated_data['fecha_inicio'],
            fecha_fin=validated_data['fecha_fin'],
            observaciones=validated_data.get('observaciones', ''),
            creado_por=self.context['request'].user,
        )
        self._guardar_detalles(nomina, detalles_data)
        return nomina

    def update(self, instance, validated_data):
        if instance.cerrada:
            raise serializers.ValidationError('No se puede editar una nómina cerrada.')

        detalles_data = validated_data.pop('detalles', None)
        if 'fecha_inicio' in validated_data:
            instance.fecha_inicio = validated_data['fecha_inicio']
        if 'fecha_fin' in validated_data:
            instance.fecha_fin = validated_data['fecha_fin']
        if 'observaciones' in validated_data:
            instance.observaciones = validated_data['observaciones']
        instance.full_clean()
        instance.save()

        if detalles_data is not None:
            self._guardar_detalles(instance, detalles_data)

        return instance



class DeudorSerializer(serializers.ModelSerializer):
    usuario = UsuarioResponsableSerializer(read_only=True)
    deudas_abiertas = serializers.SerializerMethodField()

    class Meta:
        model = Deudor
        fields = ['id', 'nombre', 'tipo', 'telefono', 'notas', 'activo', 'usuario', 'creado', 'deudas_abiertas']
        read_only_fields = ['id', 'usuario', 'creado', 'deudas_abiertas']

    def get_deudas_abiertas(self, obj):
        abiertas = self.context.get('abiertas_por_deudor')
        if abiertas is None:
            return Deuda.objects.abiertas().filter(deudor=obj).count()
        return abiertas.get(obj.id, 0)

    def validate_nombre(self, value):
        value = ' '.join(value.split())
        if not value:
            raise serializers.ValidationError('El nombre es obligatorio.')
        return value

    def create(self, validated_data):
        validated_data['usuario'] = _usuario_sesion(self)
        return super().create(validated_data)


class AbonoSerializer(serializers.ModelSerializer):
    """Un abono es el ingreso de caja ligado a la deuda (una sola divisa)."""
    cantidad = serializers.SerializerMethodField()
    usuario = UsuarioResponsableSerializer(read_only=True)
    usuario_cancelacion = UsuarioResponsableSerializer(read_only=True)

    class Meta:
        model = MovimientoTesoreria
        fields = [
            'id', 'folio', 'fecha', 'concepto', 'cantidad', 'usuario', 'creado',
            'cancelado', 'fecha_cancelacion', 'motivo_cancelacion', 'usuario_cancelacion',
        ]

    def get_cantidad(self, obj):
        return str(sum((linea.cantidad for linea in obj.divisas.all()), Decimal('0')))


class DeudaSerializer(serializers.ModelSerializer):
    deudor = serializers.SerializerMethodField()
    deudor_id = serializers.PrimaryKeyRelatedField(source='deudor', queryset=Deudor.objects.all(), write_only=True)
    divisa = DivisaSerializer(read_only=True)
    divisa_id = serializers.PrimaryKeyRelatedField(
        source='divisa', queryset=Divisa.objects.filter(activa=True), write_only=True,
    )
    monto = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal('0.01'))
    abonado = serializers.SerializerMethodField()
    saldo = serializers.SerializerMethodField()
    estado = serializers.SerializerMethodField()
    abonos = serializers.SerializerMethodField()
    usuario = UsuarioResponsableSerializer(read_only=True)
    editado_por = UsuarioResponsableSerializer(read_only=True)
    usuario_cancelacion = UsuarioResponsableSerializer(read_only=True)

    class Meta:
        model = Deuda
        fields = [
            'id', 'deudor', 'deudor_id', 'concepto', 'divisa', 'divisa_id', 'monto', 'fecha', 'notas',
            'abonado', 'saldo', 'estado', 'abonos',
            'usuario', 'creado', 'editado_por', 'editado_en',
            'cancelada', 'fecha_cancelacion', 'motivo_cancelacion', 'usuario_cancelacion',
        ]
        read_only_fields = [
            'id', 'usuario', 'creado', 'editado_por', 'editado_en',
            'cancelada', 'fecha_cancelacion', 'motivo_cancelacion', 'usuario_cancelacion',
        ]

    def get_deudor(self, obj):
        return {'id': obj.deudor_id, 'nombre': obj.deudor.nombre, 'tipo': obj.deudor.tipo}

    def _abonado(self, obj):
        # Viene anotado por Deuda.objects.con_saldo(); si no, se calcula.
        abonado = getattr(obj, 'abonado', None)
        return obj.calcular_abonado() if abonado is None else abonado

    def get_abonado(self, obj):
        return str(self._abonado(obj).quantize(Decimal('0.01')))

    def get_saldo(self, obj):
        return str((obj.monto - self._abonado(obj)).quantize(Decimal('0.01')))

    def get_estado(self, obj):
        return Deuda.estado_de(obj.cancelada, obj.monto - self._abonado(obj))

    def get_abonos(self, obj):
        abonos = sorted(obj.abonos.all(), key=lambda m: (m.fecha, m.creado))
        return AbonoSerializer(abonos, many=True).data

    def validate_concepto(self, value):
        value = ' '.join(value.split())
        if not value:
            raise serializers.ValidationError('El concepto es obligatorio.')
        return value

    def validate_fecha(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError('La fecha de la deuda no puede ser futura.')
        return value

    def create(self, validated_data):
        validated_data['usuario'] = _usuario_sesion(self)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # Con abonos de por medio el monto, la divisa o el deudor ya no se
        # pueden cambiar sin descuadrar el historial: se cancelan los abonos primero.
        if instance.cancelada:
            raise serializers.ValidationError('No se puede editar una deuda cancelada.')
        if instance.abonos_activos().exists():
            raise serializers.ValidationError('La deuda tiene abonos: ya no se puede editar.')
        hubo_cambio = any(getattr(instance, campo) != valor for campo, valor in validated_data.items())
        if hubo_cambio:
            instance.editado_por = _usuario_sesion(self)
            instance.editado_en = timezone.now()
        return super().update(instance, validated_data)
