from .models import (
    ArqueoCaja,
    ArqueoConteo,
    ArqueoDivisa,
    Banco,
    ConfiguracionFolio,
    CorteCaja,
    Denominacion,
    Divisa,
    Empleado,
    MovimientoArchivo,
    MovimientoDivisa,
    MovimientoTesoreria,
    NominaDetalle,
    NominaSemanal,
    Puesto,
    Rancho,
    SaldoCaja,
)
from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers
from django.db import transaction
from decimal import Decimal

User = get_user_model()

class UsuarioResponsableSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'last_name']

class DivisaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Divisa
        fields = ['id', 'codigo', 'nombre', 'simbolo', 'activa']


class MovimientoDivisaSerializer(serializers.ModelSerializer):
    divisa = DivisaSerializer(read_only=True)
    divisa_id = serializers.PrimaryKeyRelatedField(
        source='divisa', queryset=Divisa.objects.filter(activa=True), write_only=True
    )  # para POST/PUT: solo el id


    class Meta:
        model = MovimientoDivisa
        fields = ['id', 'divisa', 'divisa_id', 'cantidad']

class CorteCajaSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = CorteCaja
        fields = [
            'id',
            'fecha',
            'cerrado',
            'responsable_apertura',
            'responsable_cierre'
        ]

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


class MovimientoTesoreriaSerializer(serializers.ModelSerializer):
    # Declarado explícito para que DRF no agregue su UniqueValidator automático
    # (mensaje técnico); la unicidad la valida validate_folio() más abajo.
    folio = serializers.CharField(max_length=20)
    divisas = MovimientoDivisaSerializer(many=True)
    corte = CorteCajaSimpleSerializer(read_only=True)
    usuario_cancelacion = UsuarioResponsableSerializer(read_only=True)
    archivos_count = serializers.IntegerField(source='archivos.count', read_only=True)

    class Meta:
        model = MovimientoTesoreria
        fields = [
            'id', 'corte', 'fecha', 'folio', 'tipo', 'autorizo', 'beneficiario', 'concepto',
            'creado', 'modificado', 'divisas',
            'editado', 'cancelado', 'fecha_cancelacion', 'motivo_cancelacion', 'usuario_cancelacion',
            'archivos_count',
        ]
        read_only_fields = [
            'id', 'corte', 'fecha', 'creado', 'modificado', 'archivos_count',
            'editado', 'cancelado', 'fecha_cancelacion', 'motivo_cancelacion', 'usuario_cancelacion',
        ]

    def validate_folio(self, value):
        consulta = MovimientoTesoreria.objects.filter(folio=value)
        if self.instance:
            consulta = consulta.exclude(pk=self.instance.pk)
        if consulta.exists():
            raise serializers.ValidationError('Ya existe un movimiento registrado con este folio.')
        return value

    def validate(self, data):
        # Ensure divisas provided
        divisas = data.get('divisas')
        if not divisas:
            raise serializers.ValidationError({'divisas': 'Debe incluir al menos una divisa con cantidad.'})
        # Validar cantidades positivas
        for d in divisas:
            if Decimal(d.get('cantidad') or 0) <= 0:
                raise serializers.ValidationError({'divisas': 'Las cantidades deben ser mayores que cero.'})
        return data

    def create(self, validated_data):
        divisas_data = validated_data.pop("divisas")

        corte_abierto = self._obtener_corte_abierto()

        validated_data["corte"] = corte_abierto
        validated_data["fecha"] = corte_abierto.fecha
        validated_data["usuario"] = self.context["request"].user
 

        tipo = validated_data["tipo"]

        if tipo == MovimientoTesoreria.EGRESO:
            for item in divisas_data:
                saldo = SaldoCaja.objects.get(
                    corte=corte_abierto,
                    divisa=item["divisa"]
                )

                if not saldo.tiene_saldo(item["cantidad"]):
                    raise serializers.ValidationError(
                        f"Saldo insuficiente para la divisa {saldo.divisa.codigo}. "
                        f"Disponible: {saldo.saldo_disponible}"
                    )
        with transaction.atomic():
            movimiento = MovimientoTesoreria.objects.create(**validated_data)

            for item in divisas_data:
                MovimientoDivisa.objects.create(
                    movimiento=movimiento,
                    divisa=item["divisa"],
                    cantidad=item["cantidad"]
                )

                saldo = SaldoCaja.objects.get(
                    corte=corte_abierto,
                    divisa=item["divisa"]
                )
                saldo.actualizar_balance()

            _avanzar_folio(tipo, movimiento.folio)

        return movimiento


    def update(self, instance, validated_data):
        if instance.cancelado:
            raise serializers.ValidationError('No se puede editar un movimiento cancelado.')
        if instance.corte and instance.corte.cerrado:
            raise serializers.ValidationError('No se puede editar un movimiento de un corte cerrado.')

        divisas_data = validated_data.pop('divisas')
        campos_simples = ['folio', 'tipo', 'autorizo', 'beneficiario', 'concepto']
        hubo_cambio = False

        for campo in campos_simples:
            if campo in validated_data and getattr(instance, campo) != validated_data[campo]:
                hubo_cambio = True
            if campo in validated_data:
                setattr(instance, campo, validated_data[campo])

        divisas_afectadas = set()

        with transaction.atomic():
            lineas_actuales = {linea.divisa_id: linea for linea in instance.divisas.all()}
            ids_nuevos = set()

            for item in divisas_data:
                divisa = item['divisa']
                cantidad = item['cantidad']
                ids_nuevos.add(divisa.id)
                divisas_afectadas.add(divisa.id)

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
                    divisas_afectadas.add(divisa_id)
                    linea.delete()

            if hubo_cambio:
                instance.editado = True

            instance.save()

            if instance.tipo == MovimientoTesoreria.EGRESO and instance.corte:
                for divisa_id in divisas_afectadas:
                    saldo = SaldoCaja.objects.get(corte=instance.corte, divisa_id=divisa_id)
                    if saldo.saldo_disponible < 0:
                        raise serializers.ValidationError(
                            f"Saldo insuficiente para la divisa {saldo.divisa.codigo} tras la edición. "
                            f"Disponible: {saldo.saldo_disponible}"
                        )

        return instance

    def _obtener_corte_abierto(self):
        corte = CorteCaja.abierto()

        if not corte:
            raise serializers.ValidationError(
                "No hay un corte de caja abierto. Abre uno antes de registrar movimientos."
            )

        return corte


class SaldoCajaSerializer(serializers.ModelSerializer):
    divisa = DivisaSerializer(read_only=True)

    class Meta:
        model = SaldoCaja
        fields = ['id', 'corte', 'divisa', 'saldo_inicial', 'saldo_final', 'saldo_fisico', 'diferencia']


class CorteCajaSerializer(serializers.ModelSerializer):
    saldos = SaldoCajaSerializer(many=True, read_only=True)
    # NUEVO: Creamos un campo específico para enviar el nombre al frontend
    responsable_apertura = UsuarioResponsableSerializer(read_only=True)
    responsable_cierre = UsuarioResponsableSerializer(read_only=True)

    class Meta:
        model = CorteCaja
        fields = ['id', 'fecha', 'cerrado', 'fecha_cierre', 'responsable_apertura', 'responsable_cierre', 'observaciones', 'saldos']
        read_only_fields = ['id', 'saldos']

    def create(self, validated_data):
        usuario_actual = self.context["request"].user
        # Use model class method; it will validate existence of open cut
        try:
            return CorteCaja.abrir_nuevo_corte(
                fecha=validated_data.get('fecha'),
                responsable_apertura=usuario_actual,
                observaciones=validated_data.get('observaciones', ''),
            )
        except DjangoValidationError as exc:
            # normaliza a algo que DRF sí traduce en 400 con "detail"
            raise serializers.ValidationError({'detail': exc.messages[0]})


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
    corte = CorteCajaSimpleSerializer(read_only=True)
    divisas = ArqueoDivisaSerializer(many=True)
    leyenda_totales = serializers.CharField(read_only=True)

    class Meta:
        model = ArqueoCaja
        fields = [
            'id', 'corte', 'hora_inicio', 'hora_termino', 'usuario',
            'observaciones', 'creado', 'modificado', 'divisas', 'leyenda_totales',
        ]
        read_only_fields = ['id', 'corte', 'hora_termino', 'usuario', 'creado', 'modificado']

    def validate(self, data):
        divisas_data = data.get('divisas')
        if not divisas_data:
            raise serializers.ValidationError({'divisas': 'Debe incluir el conteo de todas las divisas activas.'})

        divisa_ids_enviados = {item['divisa'].id for item in divisas_data}
        divisa_ids_activas = set(Divisa.objects.filter(activa=True).values_list('id', flat=True))
        faltantes = divisa_ids_activas - divisa_ids_enviados
        if faltantes:
            codigos = Divisa.objects.filter(id__in=faltantes).values_list('codigo', flat=True)
            raise serializers.ValidationError({
                'divisas': f"Falta el conteo de: {', '.join(codigos)}."
            })

        for item in divisas_data:
            divisa = item['divisa']
            for conteo in item['conteos']:
                if conteo['denominacion'].divisa_id != divisa.id:
                    raise serializers.ValidationError({
                        'divisas': f"La denominación {conteo['denominacion']} no corresponde a {divisa.codigo}."
                    })
        return data

    def _obtener_corte_abierto(self):
        corte = CorteCaja.abierto()
        if not corte:
            raise serializers.ValidationError({
                'detail': 'No hay un corte de caja abierto. Abre uno antes de hacer un arqueo.'
            })
        return corte

    def _guardar_divisas(self, arqueo, divisas_data, corte):
        divisas_previas = {linea.divisa_id: linea for linea in arqueo.divisas.all()} if arqueo.pk else {}
        ids_enviados = set()

        for item in divisas_data:
            divisa = item['divisa']
            conteos_data = item['conteos']
            ids_enviados.add(divisa.id)

            saldo_caja, _ = SaldoCaja.objects.get_or_create(
                corte=corte,
                divisa=divisa,
                defaults={'saldo_inicial': Decimal('0'), 'saldo_final': Decimal('0')},
            )

            arqueo_divisa = divisas_previas.get(divisa.id)
            if arqueo_divisa:
                arqueo_divisa.conteos.all().delete()
            else:
                arqueo_divisa = ArqueoDivisa(arqueo=arqueo, divisa=divisa)

            arqueo_divisa.saldo_inicial = saldo_caja.saldo_inicial
            arqueo_divisa.resultado_esperado = saldo_caja.saldo_disponible
            arqueo_divisa.save()

            for conteo in conteos_data:
                ArqueoConteo.objects.create(
                    arqueo_divisa=arqueo_divisa,
                    denominacion=conteo['denominacion'],
                    piezas=conteo['piezas'],
                )

            arqueo_divisa.recalcular_total()

        for divisa_id, arqueo_divisa in divisas_previas.items():
            if divisa_id not in ids_enviados:
                arqueo_divisa.delete()

    def create(self, validated_data):
        divisas_data = validated_data.pop('divisas')
        corte = self._obtener_corte_abierto()

        # Un solo arqueo por corte: si ya existe uno para el corte abierto,
        # este "create" se convierte en un update sobre ese mismo arqueo
        # (defensa adicional a la restricción unique=True de la base de datos).
        existente = ArqueoCaja.objects.filter(corte=corte).first()
        if existente:
            return self.update(existente, {**validated_data, 'divisas': divisas_data})

        with transaction.atomic():
            arqueo = ArqueoCaja.objects.create(
                corte=corte,
                hora_inicio=validated_data['hora_inicio'],
                hora_termino=timezone.now(),
                usuario=self.context['request'].user,
                observaciones=validated_data.get('observaciones', ''),
            )
            self._guardar_divisas(arqueo, divisas_data, corte)

        return arqueo

    def update(self, instance, validated_data):
        if instance.corte.cerrado:
            raise serializers.ValidationError({'detail': 'No se puede editar un arqueo de un corte cerrado.'})

        divisas_data = validated_data.pop('divisas')
        corte = instance.corte

        with transaction.atomic():
            if 'observaciones' in validated_data:
                instance.observaciones = validated_data['observaciones']
            if 'hora_inicio' in validated_data:
                instance.hora_inicio = validated_data['hora_inicio']
            instance.hora_termino = timezone.now()
            instance.save()
            self._guardar_divisas(instance, divisas_data, corte)

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

