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


class EmpresaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Empresa
        fields = ['id', 'nombre']


class CamaraSerializer(serializers.ModelSerializer):
    class Meta:
        model = Camara
        fields = ['id', 'nombre', 'ubicacion', 'tipo', 'empresa', 'activo']


class ProveedorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Proveedor
        fields = ['id', 'nombre', 'activo']


class ClienteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cliente
        fields = ['id', 'nombre', 'activo']


class ProductoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Producto
        fields = ['id', 'talla', 'tipo', 'categoria', 'presentacion', 'activo']


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
    producto = ProductoSerializer(read_only=True)
    producto_id = serializers.PrimaryKeyRelatedField(
        source='producto', queryset=Producto.objects.all(), write_only=True
    )

    class Meta:
        model = EntradaDetalle
        fields = [
            'id', 'producto', 'producto_id', 'lote_general', 'lote_proveedor',
            'camara', 'cajas', 'peso_por_caja', 'total_kilos', 'costo_por_kilo',
            'precio_venta_planeado', 'observaciones',
        ]


class EntradaSerializer(serializers.ModelSerializer):
    proveedor = ProveedorSerializer(read_only=True)
    proveedor_id = serializers.PrimaryKeyRelatedField(
        source='proveedor', queryset=Proveedor.objects.filter(activo=True), write_only=True
    )
    detalles = EntradaDetalleNestedSerializer(many=True)

    class Meta:
        model = Entrada
        fields = ['id', 'fecha', 'proveedor', 'proveedor_id', 'factura', 'pedimento', 'detalles']

    def validate(self, data):
        detalles = data.get('detalles')
        if not detalles:
            raise serializers.ValidationError({'detalles': 'Debe incluir al menos una línea.'})
        return data

    def create(self, validated_data):
        detalles_data = validated_data.pop('detalles')
        with transaction.atomic():
            entrada = Entrada.objects.create(**validated_data)
            for item in detalles_data:
                EntradaDetalle.objects.create(entrada=entrada, **item)
        return entrada


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
    producto = ProductoSerializer(read_only=True)
    producto_id = serializers.PrimaryKeyRelatedField(
        source='producto', queryset=Producto.objects.all(), write_only=True
    )

    class Meta:
        model = SalidaDetalle
        fields = [
            'id', 'producto', 'producto_id', 'entrada_detalle', 'camara', 'cajas',
            'total_kilos', 'factura_proveedor', 'precio_x_kilo', 'total_venta',
        ]


class SalidaSerializer(serializers.ModelSerializer):
    cliente = ClienteSerializer(read_only=True)
    cliente_id = serializers.PrimaryKeyRelatedField(
        source='cliente', queryset=Cliente.objects.filter(activo=True), write_only=True
    )
    detalles = SalidaDetalleNestedSerializer(many=True)

    class Meta:
        model = Salida
        fields = ['id', 'folio_de_salida', 'cliente', 'cliente_id', 'fecha', 'notas', 'detalles']

    def validate(self, data):
        detalles = data.get('detalles')
        if not detalles:
            raise serializers.ValidationError({'detalles': 'Debe incluir al menos una línea.'})
        return data

    def create(self, validated_data):
        detalles_data = validated_data.pop('detalles')
        with transaction.atomic():
            salida = Salida.objects.create(**validated_data)
            for item in detalles_data:
                SalidaDetalle.objects.create(salida=salida, **item)
        return salida


class MovimientoCamaraSerializer(serializers.ModelSerializer):
    class Meta:
        model = MovimientoCamara
        fields = [
            'id', 'entrada_detalle_origen', 'salida_detalle', 'entrada_detalle_destino',
            'camara_origen', 'camara_destino', 'fecha', 'cajas',
        ]
