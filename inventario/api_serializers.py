from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers

from .models import (
    Camara,
    Cliente,
    Empresa,
    Entrada,
    EntradaDetalle,
    LoteGeneral,
    MovimientoCamara,
    Producto,
    Proveedor,
    Salida,
    SalidaDetalle,
)

User = get_user_model()


class UsuarioCreadorSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'last_name']


class EmpresaSerializer(serializers.ModelSerializer):
    creado_por = UsuarioCreadorSerializer(read_only=True)

    class Meta:
        model = Empresa
        fields = ['id', 'nombre', 'creado_por']


class CamaraSerializer(serializers.ModelSerializer):
    creado_por = UsuarioCreadorSerializer(read_only=True)

    class Meta:
        model = Camara
        fields = ['id', 'nombre', 'ubicacion', 'tipo', 'empresa', 'activo', 'creado_por']


class ProveedorSerializer(serializers.ModelSerializer):
    creado_por = UsuarioCreadorSerializer(read_only=True)

    class Meta:
        model = Proveedor
        fields = ['id', 'nombre', 'activo', 'creado_por']


class ClienteSerializer(serializers.ModelSerializer):
    creado_por = UsuarioCreadorSerializer(read_only=True)

    class Meta:
        model = Cliente
        fields = ['id', 'nombre', 'activo', 'creado_por']


class ProductoSerializer(serializers.ModelSerializer):
    creado_por = UsuarioCreadorSerializer(read_only=True)

    class Meta:
        model = Producto
        fields = ['id', 'talla', 'tipo', 'categoria', 'presentacion', 'activo', 'creado_por']


class LoteGeneralSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoteGeneral
        fields = ['id', 'codigo', 'entrada', 'camara', 'fecha_recibo']


class EntradaDetalleSerializer(serializers.ModelSerializer):
    producto = ProductoSerializer(read_only=True)
    producto_id = serializers.PrimaryKeyRelatedField(
        source='producto', queryset=Producto.objects.all(), write_only=True
    )

    class Meta:
        model = EntradaDetalle
        fields = [
            'id', 'entrada', 'producto', 'producto_id', 'lote_general', 'lote_proveedor',
            'camara', 'cajas', 'peso_por_caja', 'total_kilos', 'costo_por_kilo',
            'precio_venta_planeado', 'observaciones',
        ]


class EntradaDetalleNestedSerializer(serializers.ModelSerializer):
    # No read_only: al editar una Entrada, un id presente identifica la línea
    # existente a actualizar; ausente, es una línea nueva. Ver EntradaSerializer.update().
    id = serializers.IntegerField(required=False)
    producto = ProductoSerializer(read_only=True)
    producto_id = serializers.PrimaryKeyRelatedField(
        source='producto', queryset=Producto.objects.all(), write_only=True
    )
    cajas_disponibles = serializers.ReadOnlyField()
    lote_general_codigo = serializers.SerializerMethodField()

    class Meta:
        model = EntradaDetalle
        fields = [
            'id', 'producto', 'producto_id', 'lote_general', 'lote_general_codigo', 'lote_proveedor',
            'camara', 'cajas', 'peso_por_caja', 'total_kilos', 'costo_por_kilo',
            'precio_venta_planeado', 'observaciones', 'cajas_disponibles',
        ]

    def get_lote_general_codigo(self, obj):
        return obj.lote_general.codigo if obj.lote_general_id else None


class EntradaSerializer(serializers.ModelSerializer):
    proveedor = ProveedorSerializer(read_only=True)
    proveedor_id = serializers.PrimaryKeyRelatedField(
        source='proveedor', queryset=Proveedor.objects.filter(activo=True),
        write_only=True, required=False, allow_null=True,
    )
    detalles = EntradaDetalleNestedSerializer(many=True)
    creado_por = UsuarioCreadorSerializer(read_only=True)
    # No es un campo del modelo Entrada — representa el código de RECIBO INGRESO
    # (LoteGeneral) que agrupa las líneas que van a resguardo. Ver to_representation()
    # para la lectura y create()/update() para cómo se aplica.
    recibo_ingreso = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = Entrada
        fields = [
            'id', 'fecha', 'proveedor', 'proveedor_id', 'factura', 'pedimento',
            'detalles', 'recibo_ingreso', 'creado_por',
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        lote_general = instance.lotes_generales.first()
        data['recibo_ingreso'] = lote_general.codigo if lote_general else ''
        return data

    def validate(self, data):
        detalles = data.get('detalles')
        if not detalles:
            raise serializers.ValidationError({'detalles': 'Debe incluir al menos una línea.'})

        recibo_ingreso = data.get('recibo_ingreso')
        if recibo_ingreso:
            conflicto = LoteGeneral.objects.filter(codigo=recibo_ingreso)
            if self.instance:
                conflicto = conflicto.exclude(entrada=self.instance)
            if conflicto.exists():
                raise serializers.ValidationError({'recibo_ingreso': 'Ya existe un recibo de ingreso con ese código.'})

            camaras_resguardo = {item['camara'].id for item in detalles if item.get('camara')}
            if len(camaras_resguardo) > 1:
                raise serializers.ValidationError({
                    'recibo_ingreso': (
                        'Todas las líneas que van a resguardo deben ser de la misma cámara '
                        'para compartir un recibo de ingreso.'
                    )
                })
        return data

    def _aplicar_recibo_ingreso(self, entrada, recibo_ingreso, lineas):
        lineas_resguardo = [l for l in lineas if l.camara_id]
        if not lineas_resguardo:
            return
        lote_general = entrada.lotes_generales.first()
        if lote_general:
            lote_general.codigo = recibo_ingreso
            lote_general.camara = lineas_resguardo[0].camara
            lote_general.save()
        else:
            lote_general = LoteGeneral.objects.create(
                codigo=recibo_ingreso,
                entrada=entrada,
                camara=lineas_resguardo[0].camara,
                fecha_recibo=entrada.fecha,
            )
        EntradaDetalle.objects.filter(id__in=[l.id for l in lineas_resguardo]).update(lote_general=lote_general)

    def create(self, validated_data):
        detalles_data = validated_data.pop('detalles')
        recibo_ingreso = validated_data.pop('recibo_ingreso', '')
        with transaction.atomic():
            entrada = Entrada.objects.create(**validated_data)
            lineas = []
            for item in detalles_data:
                item.pop('id', None)
                lineas.append(EntradaDetalle.objects.create(entrada=entrada, **item))
            if recibo_ingreso:
                self._aplicar_recibo_ingreso(entrada, recibo_ingreso, lineas)
        return entrada

    def update(self, instance, validated_data):
        detalles_data = validated_data.pop('detalles')
        recibo_ingreso = validated_data.pop('recibo_ingreso', None)

        with transaction.atomic():
            for campo, valor in validated_data.items():
                setattr(instance, campo, valor)
            instance.save()

            ids_enviados = {item['id'] for item in detalles_data if 'id' in item}
            for linea in instance.detalles.all():
                if linea.id not in ids_enviados:
                    linea.delete()

            lineas = []
            for item in detalles_data:
                linea_id = item.pop('id', None)
                if linea_id:
                    EntradaDetalle.objects.filter(id=linea_id, entrada=instance).update(**item)
                    lineas.append(EntradaDetalle.objects.get(id=linea_id))
                else:
                    lineas.append(EntradaDetalle.objects.create(entrada=instance, **item))

            if recibo_ingreso:
                self._aplicar_recibo_ingreso(instance, recibo_ingreso, lineas)

        return instance


class SalidaDetalleSerializer(serializers.ModelSerializer):
    producto = ProductoSerializer(read_only=True)
    producto_id = serializers.PrimaryKeyRelatedField(
        source='producto', queryset=Producto.objects.all(), write_only=True
    )

    class Meta:
        model = SalidaDetalle
        fields = [
            'id', 'salida', 'producto', 'producto_id', 'entrada_detalle', 'camara', 'cajas',
            'total_kilos', 'factura_proveedor', 'precio_x_kilo', 'total_venta',
        ]


class SalidaDetalleNestedSerializer(serializers.ModelSerializer):
    # No read_only: al editar una Salida, un id presente identifica la línea
    # existente a actualizar; ausente, es una línea nueva. Ver SalidaSerializer.update().
    id = serializers.IntegerField(required=False)
    producto = ProductoSerializer(read_only=True)
    producto_id = serializers.PrimaryKeyRelatedField(
        source='producto', queryset=Producto.objects.all(), write_only=True
    )
    # Heredada del ENTRADA_DETALLE de origen, no la captura el usuario — ver
    # SalidaSerializer.create()/update(), que la calcula a partir del lote.
    factura_proveedor = serializers.CharField(read_only=True)

    class Meta:
        model = SalidaDetalle
        fields = [
            'id', 'producto', 'producto_id', 'entrada_detalle', 'camara', 'cajas',
            'total_kilos', 'factura_proveedor', 'precio_x_kilo', 'total_venta',
        ]


class SalidaSerializer(serializers.ModelSerializer):
    cliente = ClienteSerializer(read_only=True)
    cliente_id = serializers.PrimaryKeyRelatedField(
        source='cliente', queryset=Cliente.objects.filter(activo=True),
        write_only=True, required=False, allow_null=True,
    )
    detalles = SalidaDetalleNestedSerializer(many=True)
    creado_por = UsuarioCreadorSerializer(read_only=True)

    class Meta:
        model = Salida
        fields = ['id', 'folio_de_salida', 'cliente', 'cliente_id', 'fecha', 'notas', 'detalles', 'creado_por']

    def validate(self, data):
        detalles = data.get('detalles')
        if not detalles:
            raise serializers.ValidationError({'detalles': 'Debe incluir al menos una línea.'})

        # Al editar, las cajas que esta misma Salida ya tenía reservadas de un lote se
        # "liberan" antes de comparar, porque se van a reemplazar por lo que viene en el payload.
        ya_reservado = {}
        if self.instance:
            for linea in self.instance.detalles.all():
                ya_reservado[linea.entrada_detalle_id] = ya_reservado.get(linea.entrada_detalle_id, 0) + linea.cajas

        solicitado = {}
        lotes = {}
        for item in detalles:
            lote = item['entrada_detalle']
            lotes[lote.id] = lote
            solicitado[lote.id] = solicitado.get(lote.id, 0) + item['cajas']

        for lote_id, cajas_pedidas in solicitado.items():
            lote = lotes[lote_id]
            disponibles = lote.cajas_disponibles + ya_reservado.get(lote_id, 0)
            if cajas_pedidas > disponibles:
                raise serializers.ValidationError({
                    'detalles': (
                        f"No hay suficientes cajas disponibles en el lote {lote.lote_proveedor} "
                        f"({lote.producto}): pediste {cajas_pedidas}, disponibles {disponibles}."
                    )
                })
        return data

    def create(self, validated_data):
        detalles_data = validated_data.pop('detalles')
        with transaction.atomic():
            salida = Salida.objects.create(**validated_data)
            for item in detalles_data:
                item.pop('id', None)
                item['factura_proveedor'] = self._factura_del_lote(item['entrada_detalle'])
                SalidaDetalle.objects.create(salida=salida, **item)
        return salida

    def update(self, instance, validated_data):
        detalles_data = validated_data.pop('detalles')

        with transaction.atomic():
            for campo, valor in validated_data.items():
                setattr(instance, campo, valor)
            instance.save()

            ids_enviados = {item['id'] for item in detalles_data if 'id' in item}
            for linea in instance.detalles.all():
                if linea.id not in ids_enviados:
                    linea.delete()

            for item in detalles_data:
                linea_id = item.pop('id', None)
                item['factura_proveedor'] = self._factura_del_lote(item['entrada_detalle'])
                if linea_id:
                    SalidaDetalle.objects.filter(id=linea_id, salida=instance).update(**item)
                else:
                    SalidaDetalle.objects.create(salida=instance, **item)

        return instance

    @staticmethod
    def _factura_del_lote(entrada_detalle):
        entrada = entrada_detalle.entrada
        return entrada.factura if entrada.proveedor_id else ''


class MovimientoCamaraSerializer(serializers.ModelSerializer):
    creado_por = UsuarioCreadorSerializer(read_only=True)

    class Meta:
        model = MovimientoCamara
        fields = [
            'id', 'entrada_detalle_origen', 'salida_detalle', 'entrada_detalle_destino',
            'camara_origen', 'camara_destino', 'fecha', 'cajas', 'creado_por',
        ]


class MovimientoCamaraCrearSerializer(serializers.Serializer):
    """
    Un movimiento entre cámaras no es un modelo que se crea directo: es un traslado
    puramente físico, así que orquesta en una sola transacción la salida del lote de
    origen y la entrada del mismo lote en la cámara destino — sin proveedor ni cliente,
    porque no es una compra ni una venta real (ver spec de separación Compra/Venta).
    """
    entrada_detalle_origen = serializers.PrimaryKeyRelatedField(queryset=EntradaDetalle.objects.all())
    camara_destino = serializers.PrimaryKeyRelatedField(queryset=Camara.objects.filter(activo=True))
    fecha = serializers.DateField()
    cajas = serializers.IntegerField(min_value=1)
    total_kilos = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)

    def validate(self, data):
        origen = data['entrada_detalle_origen']
        if not origen.camara_id:
            raise serializers.ValidationError({
                'entrada_detalle_origen': 'Este lote no tiene cámara de origen (fue venta directa), no se puede mover.'
            })
        if data['camara_destino'].id == origen.camara_id:
            raise serializers.ValidationError({'camara_destino': 'La cámara destino debe ser distinta de la de origen.'})
        if data['cajas'] > origen.cajas_disponibles:
            raise serializers.ValidationError({
                'cajas': (
                    f"No hay suficientes cajas disponibles en el lote {origen.lote_proveedor} "
                    f"({origen.producto}): pediste {data['cajas']}, disponibles {origen.cajas_disponibles}."
                )
            })
        return data

    def create(self, validated_data):
        origen = validated_data['entrada_detalle_origen']
        camara_destino = validated_data['camara_destino']
        fecha = validated_data['fecha']
        cajas = validated_data['cajas']
        total_kilos = validated_data['total_kilos']
        creado_por = validated_data.get('creado_por')

        with transaction.atomic():
            salida = Salida.objects.create(
                folio_de_salida=f"MOV-{uuid4().hex[:8].upper()}",
                cliente=None,
                fecha=fecha,
                notas="Movimiento entre cámaras",
                creado_por=creado_por,
            )
            salida_detalle = SalidaDetalle.objects.create(
                salida=salida,
                producto=origen.producto,
                entrada_detalle=origen,
                camara=origen.camara,
                cajas=cajas,
                total_kilos=total_kilos,
                factura_proveedor=origen.entrada.factura if origen.entrada.proveedor_id else '',
            )

            entrada_destino = Entrada.objects.create(
                fecha=fecha, proveedor=None, factura='', pedimento='', creado_por=creado_por,
            )
            entrada_detalle_destino = EntradaDetalle.objects.create(
                entrada=entrada_destino,
                producto=origen.producto,
                lote_proveedor=origen.lote_proveedor,
                camara=camara_destino,
                cajas=cajas,
                peso_por_caja=origen.peso_por_caja,
                total_kilos=total_kilos,
                costo_por_kilo=origen.costo_por_kilo,
                precio_venta_planeado=origen.precio_venta_planeado,
            )

            movimiento = MovimientoCamara.objects.create(
                entrada_detalle_origen=origen,
                salida_detalle=salida_detalle,
                entrada_detalle_destino=entrada_detalle_destino,
                camara_origen=origen.camara,
                camara_destino=camara_destino,
                fecha=fecha,
                cajas=cajas,
                creado_por=creado_por,
            )

        return movimiento
