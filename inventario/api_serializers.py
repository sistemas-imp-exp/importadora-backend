from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers

from .auditoria import entrada_tiene_salidas, registrar_cambios, tomar_snapshot
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


def _mayusculas(valor):
    """Normaliza texto libre a mayúsculas para evitar duplicados por captura
    inconsistente (ej. 'Coexmar' vs 'COEXMAR'), el mismo problema que arrastraba
    el Excel origen."""
    return valor.strip().upper() if isinstance(valor, str) else valor


class UsuarioCreadorSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'last_name']


class EmpresaSerializer(serializers.ModelSerializer):
    creado_por = UsuarioCreadorSerializer(read_only=True)

    class Meta:
        model = Empresa
        fields = ['id', 'nombre', 'creado_por']

    def validate_nombre(self, value):
        return _mayusculas(value)


class CamaraSerializer(serializers.ModelSerializer):
    creado_por = UsuarioCreadorSerializer(read_only=True)

    class Meta:
        model = Camara
        fields = ['id', 'nombre', 'ubicacion', 'tipo', 'empresa', 'activo', 'creado_por']

    def validate_nombre(self, value):
        return _mayusculas(value)

    def validate_ubicacion(self, value):
        return _mayusculas(value)


class ProveedorSerializer(serializers.ModelSerializer):
    creado_por = UsuarioCreadorSerializer(read_only=True)

    class Meta:
        model = Proveedor
        fields = ['id', 'nombre', 'activo', 'creado_por']

    def validate_nombre(self, value):
        return _mayusculas(value)


class ClienteSerializer(serializers.ModelSerializer):
    creado_por = UsuarioCreadorSerializer(read_only=True)

    class Meta:
        model = Cliente
        fields = ['id', 'nombre', 'activo', 'creado_por']

    def validate_nombre(self, value):
        return _mayusculas(value)


class ProductoSerializer(serializers.ModelSerializer):
    creado_por = UsuarioCreadorSerializer(read_only=True)

    class Meta:
        model = Producto
        fields = ['id', 'talla', 'tipo', 'categoria', 'presentacion', 'activo', 'creado_por']

    def validate_talla(self, value):
        return _mayusculas(value)

    def validate_tipo(self, value):
        return _mayusculas(value)

    def validate_categoria(self, value):
        return _mayusculas(value)


class LoteGeneralSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoteGeneral
        fields = ['id', 'codigo', 'entrada', 'camara', 'fecha_recibo']


# Al anidarse dentro de Entradas/Salidas, los catálogos viajan una vez por LÍNEA.
# Estas versiones omiten `creado_por` (un objeto anidado extra por fila, solo para
# mostrar un nombre que estas tablas no usan) y los campos de auditoría: el
# catálogo completo se sigue sirviendo en su propio endpoint.
class ProductoMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Producto
        fields = ['id', 'talla', 'tipo', 'categoria', 'presentacion', 'activo']


class ProveedorMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Proveedor
        fields = ['id', 'nombre', 'activo']


class ClienteMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cliente
        fields = ['id', 'nombre', 'activo']


class EntradaDetalleSerializer(serializers.ModelSerializer):
    producto = ProductoMiniSerializer(read_only=True)
    producto_id = serializers.PrimaryKeyRelatedField(
        source='producto', queryset=Producto.objects.all(), write_only=True
    )
    # Se calcula en el backend a partir de la entrada (o del lote de origen si viene de
    # un MovimientoCamara) — no lo captura el usuario. Ver EntradaSerializer.create().
    proveedor_origen = ProveedorMiniSerializer(read_only=True)

    class Meta:
        model = EntradaDetalle
        fields = [
            'id', 'entrada', 'producto', 'producto_id', 'lote_general', 'lote_proveedor',
            'camara', 'cajas', 'peso_por_caja', 'total_kilos', 'costo_por_kilo',
            'precio_venta_planeado', 'fecha_caducidad', 'observaciones', 'proveedor_origen',
        ]

    def validate_lote_proveedor(self, value):
        return _mayusculas(value)

    def validate_observaciones(self, value):
        return _mayusculas(value)


class EntradaDetalleNestedSerializer(serializers.ModelSerializer):
    # No read_only: al editar una Entrada, un id presente identifica la línea
    # existente a actualizar; ausente, es una línea nueva. Ver EntradaSerializer.update().
    id = serializers.IntegerField(required=False)
    producto = ProductoMiniSerializer(read_only=True)
    producto_id = serializers.PrimaryKeyRelatedField(
        source='producto', queryset=Producto.objects.all(), write_only=True
    )
    cajas_disponibles = serializers.ReadOnlyField()
    kilos_disponibles = serializers.ReadOnlyField()
    lote_general_codigo = serializers.SerializerMethodField()
    # Se calcula en el backend a partir de la entrada (o del lote de origen si viene de
    # un MovimientoCamara) — no lo captura el usuario. Ver EntradaSerializer.create().
    proveedor_origen = ProveedorMiniSerializer(read_only=True)
    # Factura y recibo del lote raíz: un lote movido de cámara vive en una entrada
    # sin documentos propios, y sin esto sería imposible encontrarlo al registrar
    # una salida (que ahora se busca por factura o recibo). Ver EntradaDetalle.documento_origen.
    factura_origen = serializers.SerializerMethodField()
    recibo_origen = serializers.SerializerMethodField()

    class Meta:
        model = EntradaDetalle
        fields = [
            'id', 'producto', 'producto_id', 'lote_general', 'lote_general_codigo', 'lote_proveedor',
            'camara', 'cajas', 'peso_por_caja', 'total_kilos', 'costo_por_kilo',
            'precio_venta_planeado', 'fecha_caducidad', 'observaciones', 'cajas_disponibles',
            'kilos_disponibles', 'proveedor_origen', 'factura_origen', 'recibo_origen',
        ]

    def get_lote_general_codigo(self, obj):
        return obj.lote_general.codigo if obj.lote_general_id else None

    def get_factura_origen(self, obj) -> str:
        return obj.documento_origen['factura']

    def get_recibo_origen(self, obj) -> str:
        return obj.documento_origen['recibo']

    def validate_lote_proveedor(self, value):
        return _mayusculas(value)

    def validate_observaciones(self, value):
        return _mayusculas(value)


class EntradaSerializer(serializers.ModelSerializer):
    proveedor = ProveedorMiniSerializer(read_only=True)
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
    # ¿La entrada fue modificada después de registrarse? Se deduce de la bitácora
    # (EdicionEntrada), no de una bandera aparte, así que no puede desincronizarse
    # de lo que realmente muestra Auditoría de entradas.
    editado = serializers.SerializerMethodField()

    class Meta:
        model = Entrada
        fields = [
            'id', 'fecha', 'proveedor', 'proveedor_id', 'es_internacional', 'factura', 'pedimento',
            'detalles', 'recibo_ingreso', 'creado_por', 'editado',
        ]

    def get_editado(self, obj) -> bool:
        # El ViewSet anota `fue_editada` para no disparar una consulta por fila;
        # el fallback cubre a quien use el serializer con un objeto suelto.
        anotado = getattr(obj, 'fue_editada', None)
        return bool(anotado) if anotado is not None else obj.ediciones.exists()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # .all()[0] y no .first(): con prefetch_related('lotes_generales') esto lee
        # la caché ya cargada, mientras que .first() vuelve a consultar por entrada.
        lotes = list(instance.lotes_generales.all())
        data['recibo_ingreso'] = lotes[0].codigo if lotes else ''
        return data

    def validate_factura(self, value):
        return _mayusculas(value)

    def validate_pedimento(self, value):
        return _mayusculas(value)

    def validate_recibo_ingreso(self, value):
        return _mayusculas(value)

    def validate(self, data):
        detalles = data.get('detalles')
        if not detalles:
            raise serializers.ValidationError({'detalles': 'Debe incluir al menos una línea.'})

        # Una entrada con salidas ya sostiene existencias y documentos de venta:
        # editarla por esta vía permitiría dejar cajas/kilos disponibles en
        # negativo o mover mercancía de cámara sin MovimientoCamara. Las
        # correcciones que no tocan esa contabilidad van por la vía auditada de
        # superusuario (ver inventario/auditoria.py).
        if self.instance and entrada_tiene_salidas(self.instance):
            raise serializers.ValidationError({
                'detalles': (
                    'Esta entrada ya tiene salidas o movimientos entre cámaras registrados, '
                    'así que no puede editarse. Un superusuario puede corregir factura, lote, '
                    'caducidad, precio de venta y observaciones desde Auditoría de entradas.'
                )
            })

        es_internacional = data.get(
            'es_internacional', getattr(self.instance, 'es_internacional', False)
        )
        factura = data.get('factura', getattr(self.instance, 'factura', ''))
        pedimento = data.get('pedimento', getattr(self.instance, 'pedimento', ''))
        if not factura.strip():
            raise serializers.ValidationError({'factura': 'La factura es obligatoria.'})
        if es_internacional and not pedimento.strip():
            raise serializers.ValidationError({
                'pedimento': 'El pedimento es obligatorio en una entrada internacional.'
            })

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
            request = self.context.get('request')
            creado_por = getattr(request, 'user', None) if request else None
            lote_general = LoteGeneral.objects.create(
                codigo=recibo_ingreso,
                entrada=entrada,
                camara=lineas_resguardo[0].camara,
                fecha_recibo=entrada.fecha,
                creado_por=creado_por,
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
                item['proveedor_origen'] = entrada.proveedor
                lineas.append(EntradaDetalle.objects.create(entrada=entrada, **item))
            if recibo_ingreso:
                self._aplicar_recibo_ingreso(entrada, recibo_ingreso, lineas)
        return entrada

    def update(self, instance, validated_data):
        detalles_data = validated_data.pop('detalles')
        recibo_ingreso = validated_data.pop('recibo_ingreso', None)

        # Foto previa: al terminar se compara contra el estado nuevo para dejar
        # en la bitácora qué se movió, quién y cuándo (Auditoría de entradas).
        snapshot = tomar_snapshot(instance)
        request = self.context.get('request')
        usuario = getattr(request, 'user', None) if request else None
        if usuario is not None and not usuario.is_authenticated:
            usuario = None

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
                item['proveedor_origen'] = instance.proveedor
                if linea_id:
                    EntradaDetalle.objects.filter(id=linea_id, entrada=instance).update(**item)
                    lineas.append(EntradaDetalle.objects.get(id=linea_id))
                else:
                    lineas.append(EntradaDetalle.objects.create(entrada=instance, **item))

            if recibo_ingreso:
                self._aplicar_recibo_ingreso(instance, recibo_ingreso, lineas)

            registrar_cambios(instance, snapshot, usuario)

        return instance


class SalidaDetalleSerializer(serializers.ModelSerializer):
    producto = ProductoMiniSerializer(read_only=True)
    producto_id = serializers.PrimaryKeyRelatedField(
        source='producto', queryset=Producto.objects.all(), write_only=True
    )
    # Heredados del ENTRADA_DETALLE de origen, no los captura el usuario — se
    # recalculan en create()/update() a partir del lote. Que una salida "mueva"
    # la mercancía a otra cámara solo puede pasar vía MovimientoCamara, nunca
    # editando esto a mano, o el inventario por cámara deja de ser fiel.
    camara = serializers.PrimaryKeyRelatedField(read_only=True)
    factura_proveedor = serializers.CharField(read_only=True)

    class Meta:
        model = SalidaDetalle
        fields = [
            'id', 'salida', 'producto', 'producto_id', 'entrada_detalle', 'camara', 'cajas',
            'total_kilos', 'factura_proveedor', 'precio_x_kilo', 'total_venta', 'notas',
        ]

    def validate_notas(self, value):
        return _mayusculas(value)

    def create(self, validated_data):
        entrada_detalle = validated_data['entrada_detalle']
        validated_data['camara'] = entrada_detalle.camara
        validated_data['factura_proveedor'] = SalidaSerializer._factura_del_lote(entrada_detalle)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        entrada_detalle = validated_data.get('entrada_detalle', instance.entrada_detalle)
        validated_data['camara'] = entrada_detalle.camara
        validated_data['factura_proveedor'] = SalidaSerializer._factura_del_lote(entrada_detalle)
        return super().update(instance, validated_data)


class SalidaDetalleNestedSerializer(serializers.ModelSerializer):
    # No read_only: al editar una Salida, un id presente identifica la línea
    # existente a actualizar; ausente, es una línea nueva. Ver SalidaSerializer.update().
    id = serializers.IntegerField(required=False)
    producto = ProductoMiniSerializer(read_only=True)
    producto_id = serializers.PrimaryKeyRelatedField(
        source='producto', queryset=Producto.objects.all(), write_only=True
    )
    # Heredados del ENTRADA_DETALLE de origen, no los captura el usuario — ver
    # SalidaSerializer.create()/update(), que los calcula a partir del lote. Que
    # una salida "mueva" la mercancía a otra cámara solo puede pasar vía
    # MovimientoCamara, nunca editando esto a mano en una venta.
    camara = serializers.PrimaryKeyRelatedField(read_only=True)
    factura_proveedor = serializers.CharField(read_only=True)
    # Datos del lote de origen publicados en la propia línea. Antes la tabla de
    # salidas los resolvía en el navegador recorriendo TODAS las entradas, que era
    # la razón por la que esa pantalla descargaba 2 MB extra.
    lote_proveedor = serializers.CharField(source='entrada_detalle.lote_proveedor', read_only=True)
    proveedor_nombre = serializers.SerializerMethodField()

    class Meta:
        model = SalidaDetalle
        fields = [
            'id', 'producto', 'producto_id', 'entrada_detalle', 'camara', 'cajas',
            'total_kilos', 'factura_proveedor', 'precio_x_kilo', 'total_venta', 'notas',
            'lote_proveedor', 'proveedor_nombre',
        ]

    def get_proveedor_nombre(self, obj) -> str:
        proveedor = obj.entrada_detalle.proveedor_origen
        return proveedor.nombre if proveedor else '—'

    def validate_notas(self, value):
        return _mayusculas(value)


class SalidaSerializer(serializers.ModelSerializer):
    cliente = ClienteMiniSerializer(read_only=True)
    cliente_id = serializers.PrimaryKeyRelatedField(
        source='cliente', queryset=Cliente.objects.filter(activo=True),
        write_only=True, required=False, allow_null=True,
    )
    detalles = SalidaDetalleNestedSerializer(many=True)
    creado_por = UsuarioCreadorSerializer(read_only=True)

    class Meta:
        model = Salida
        fields = ['id', 'folio_de_salida', 'cliente', 'cliente_id', 'fecha', 'notas', 'detalles', 'creado_por']

    def validate_folio_de_salida(self, value):
        return _mayusculas(value)

    def validate_notas(self, value):
        return _mayusculas(value)

    def validate(self, data):
        detalles = data.get('detalles')
        if not detalles:
            raise serializers.ValidationError({'detalles': 'Debe incluir al menos una línea.'})

        # Al editar, lo que esta misma Salida ya tenía reservado de un lote se
        # "libera" antes de comparar, porque se va a reemplazar por lo que viene
        # en el payload — igual para cajas que para kilos.
        cajas_ya_reservadas = {}
        kilos_ya_reservados = {}
        if self.instance:
            for linea in self.instance.detalles.all():
                cajas_ya_reservadas[linea.entrada_detalle_id] = (
                    cajas_ya_reservadas.get(linea.entrada_detalle_id, 0) + linea.cajas
                )
                kilos_ya_reservados[linea.entrada_detalle_id] = (
                    kilos_ya_reservados.get(linea.entrada_detalle_id, Decimal('0')) + linea.total_kilos
                )

        cajas_solicitadas = {}
        kilos_solicitados = {}
        lotes = {}
        for item in detalles:
            lote = item['entrada_detalle']
            lotes[lote.id] = lote
            cajas_solicitadas[lote.id] = cajas_solicitadas.get(lote.id, 0) + item['cajas']
            kilos_solicitados[lote.id] = kilos_solicitados.get(lote.id, Decimal('0')) + item['total_kilos']

        for lote_id, cajas_pedidas in cajas_solicitadas.items():
            lote = lotes[lote_id]
            disponibles = lote.cajas_disponibles + cajas_ya_reservadas.get(lote_id, 0)
            if cajas_pedidas > disponibles:
                raise serializers.ValidationError({
                    'detalles': (
                        f"No hay suficientes cajas disponibles en el lote {lote.lote_proveedor} "
                        f"({lote.producto}): pediste {cajas_pedidas}, disponibles {disponibles}."
                    )
                })

        # Antes solo se topaba `cajas`: con eso ya no alcanza en cuanto una línea
        # puede llevar cajas=0 (kilos sueltos de una caja abierta) — sin este
        # chequeo esa línea no tendría ningún tope real.
        for lote_id, kilos_pedidos in kilos_solicitados.items():
            lote = lotes[lote_id]
            disponibles = lote.kilos_disponibles + kilos_ya_reservados.get(lote_id, Decimal('0'))
            if kilos_pedidos > disponibles:
                raise serializers.ValidationError({
                    'detalles': (
                        f"No hay suficientes kilos disponibles en el lote {lote.lote_proveedor} "
                        f"({lote.producto}): pediste {kilos_pedidos} kg, disponibles {disponibles} kg."
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
                item['camara'] = item['entrada_detalle'].camara
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
                item['camara'] = item['entrada_detalle'].camara
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
    # Descripción del lote movido, publicada en el propio movimiento: la tabla la
    # resolvía en el navegador recorriendo todas las entradas. Además el lote de
    # origen puede haber quedado sin existencia, así que no se puede leer de la
    # foto de existencias.
    lote_origen = serializers.SerializerMethodField()

    class Meta:
        model = MovimientoCamara
        fields = [
            'id', 'entrada_detalle_origen', 'salida_detalle', 'entrada_detalle_destino',
            'camara_origen', 'camara_destino', 'fecha', 'cajas', 'creado_por', 'lote_origen',
        ]

    def get_lote_origen(self, obj) -> str:
        lote = obj.entrada_detalle_origen
        return f"{lote.producto} — lote {lote.lote_proveedor}"


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
                # Se hereda del lote de origen (no de entrada_destino.proveedor, que es
                # None a propósito) para no perder la trazabilidad al mover cámaras.
                proveedor_origen=origen.proveedor_origen,
                # La caducidad es del producto físico, no de la "entrada" — viaja con
                # el lote al moverlo de cámara, igual que su costo o su peso por caja.
                fecha_caducidad=origen.fecha_caducidad,
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
