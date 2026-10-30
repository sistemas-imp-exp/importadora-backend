from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers

from .auditoria import entrada_tiene_salidas, recibos_de, registrar_cambios, tomar_snapshot
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
        fields = ['id', 'codigo', 'camara', 'fecha_recibo']


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


class EmpresaMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Empresa
        fields = ['id', 'nombre']


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
            'factura', 'pedimento',
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
    # Proveedor de la línea: una entrada puede traer mercancía de varios.
    proveedor_origen = ProveedorMiniSerializer(read_only=True)
    proveedor_id = serializers.PrimaryKeyRelatedField(
        source='proveedor_origen', queryset=Proveedor.objects.filter(activo=True), write_only=True,
        error_messages={'required': 'Selecciona el proveedor.', 'null': 'Selecciona el proveedor.'},
    )
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
            'kilos_disponibles', 'proveedor_origen', 'proveedor_id', 'factura', 'pedimento',
            'factura_origen', 'recibo_origen',
        ]

    def get_lote_general_codigo(self, obj):
        return obj.lote_general.codigo if obj.lote_general_id else None

    def get_factura_origen(self, obj) -> str:
        return obj.documento_origen['factura']

    def get_recibo_origen(self, obj) -> str:
        return obj.documento_origen['recibo']

    def validate_lote_proveedor(self, value):
        return _mayusculas(value)

    def validate_factura(self, value):
        return _mayusculas(value)

    def validate_pedimento(self, value):
        return _mayusculas(value)

    def validate_observaciones(self, value):
        return _mayusculas(value)


class EntradaSerializer(serializers.ModelSerializer):
    empresa = EmpresaMiniSerializer(read_only=True)
    # Obligatoria en la API aunque el modelo la permita nula: esa nulidad solo
    # existe para los traslados entre cámaras, que no pasan por este serializer.
    empresa_id = serializers.PrimaryKeyRelatedField(
        source='empresa', queryset=Empresa.objects.all(), write_only=True,
        error_messages={'required': 'Selecciona la empresa.', 'null': 'Selecciona la empresa.'},
    )
    detalles = EntradaDetalleNestedSerializer(many=True)
    creado_por = UsuarioCreadorSerializer(read_only=True)
    # No es un campo del modelo Entrada — representa el código de RECIBO INGRESO
    # (LoteGeneral) que agrupa las líneas que van a cámara. Un recibo puede
    # abarcar varias entradas. Ver to_representation() para la lectura y
    # create()/update() para cómo se aplica.
    recibo_ingreso = serializers.CharField(required=False, allow_blank=True)
    # ¿La entrada fue modificada después de registrarse? Se deduce de la bitácora
    # (EdicionEntrada), no de una bandera aparte, así que no puede desincronizarse
    # de lo que realmente muestra Auditoría de entradas.
    editado = serializers.SerializerMethodField()

    class Meta:
        model = Entrada
        fields = [
            'id', 'fecha', 'empresa', 'empresa_id', 'es_internacional', 'es_traslado',
            'detalles', 'recibo_ingreso', 'creado_por', 'editado',
        ]
        read_only_fields = ['es_traslado']

    def get_editado(self, obj) -> bool:
        # El ViewSet anota `fue_editada` para no disparar una consulta por fila;
        # el fallback cubre a quien use el serializer con un objeto suelto.
        anotado = getattr(obj, 'fue_editada', None)
        return bool(anotado) if anotado is not None else obj.ediciones.exists()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Cada línea puede tener su propio recibo (ver auditoria.editar_recibos):
        # aquí se resumen todos, sin repetir.
        data['recibo_ingreso'] = recibos_de(instance)
        return data

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
        # Proveedor, factura y pedimento son de cada línea: una entrada puede
        # traer mercancía de varios proveedores, cada uno con sus documentos.
        errores_lineas = []
        for item in detalles:
            errores = {}
            if not (item.get('factura') or '').strip():
                errores['factura'] = 'La factura es obligatoria.'
            if es_internacional and not (item.get('pedimento') or '').strip():
                errores['pedimento'] = 'El pedimento es obligatorio en una compra de importación.'
            errores_lineas.append(errores)
        if any(errores_lineas):
            raise serializers.ValidationError({'detalles': errores_lineas})

        recibo_ingreso = data.get('recibo_ingreso')
        # Al editar sin tocar el recibo llega el mismo resumen que se devolvió
        # (puede ser "IMP-1, IMP-2" si cada línea tiene el suyo): no se reaplica.
        if self.instance and recibo_ingreso == recibos_de(self.instance):
            data.pop('recibo_ingreso')
        elif recibo_ingreso:
            camaras = {item['camara'] for item in detalles if item.get('camara')}
            if len(camaras) > 1:
                raise serializers.ValidationError({
                    'recibo_ingreso': (
                        'Todas las líneas que van a cámara deben ser de la misma cámara '
                        'para compartir un recibo de ingreso.'
                    )
                })
            # Un recibo puede abarcar varias entradas, siempre en la misma cámara.
            existente = LoteGeneral.objects.select_related('camara').filter(codigo=recibo_ingreso).first()
            if existente and camaras and existente.camara not in camaras:
                raise serializers.ValidationError({
                    'recibo_ingreso': f'El recibo {recibo_ingreso} ya existe en la cámara {existente.camara.nombre}.'
                })
        return data

    def _aplicar_recibo_ingreso(self, entrada, recibo_ingreso, lineas):
        lineas_camara = [l for l in lineas if l.camara_id]
        if not lineas_camara:
            return
        anteriores = {l.lote_general_id for l in lineas_camara if l.lote_general_id}
        request = self.context.get('request')
        creado_por = getattr(request, 'user', None) if request else None
        lote_general, _ = LoteGeneral.objects.get_or_create(
            codigo=recibo_ingreso,
            defaults={'camara': lineas_camara[0].camara, 'fecha_recibo': entrada.fecha, 'creado_por': creado_por},
        )
        EntradaDetalle.objects.filter(id__in=[l.id for l in lineas_camara]).update(lote_general=lote_general)
        # El recibo anterior se borra si ya no agrupa ninguna línea (libera su código).
        LoteGeneral.objects.filter(id__in=anteriores - {lote_general.id}, entradas_detalle__isnull=True).delete()

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
    lote_proveedor = serializers.SerializerMethodField()
    proveedor_nombre = serializers.SerializerMethodField()
    # Diferencia que Cartera encontró contra la nota en papel y que Inventario
    # debe corregir; desaparece sola en cuanto se corrige la línea.
    aviso_cartera = serializers.SerializerMethodField()
    # Lo que ya se entregó del resguardo de la línea (retiros desde Resguardos).
    kilos_retirados = serializers.SerializerMethodField()

    class Meta:
        model = SalidaDetalle
        fields = [
            'id', 'producto', 'producto_id', 'entrada_detalle', 'camara', 'cajas',
            'total_kilos', 'factura_proveedor', 'precio_x_kilo', 'total_venta', 'notas',
            'lote_proveedor', 'proveedor_nombre', 'aviso_cartera',
            'cajas_resguardo', 'kilos_resguardo', 'kilos_retirados', 'retiro_de',
        ]
        # El resguardo lo fija SalidaSerializer: todo en una venta en resguardo, nada en una normal.
        read_only_fields = ['retiro_de', 'cajas_resguardo', 'kilos_resguardo']
        extra_kwargs = {'entrada_detalle': {'required': False, 'allow_null': True}}

    def get_kilos_retirados(self, obj) -> str:
        # SalidaViewSet trae los retiros con la línea; se suman aquí sin otra consulta.
        return str(sum((r.total_kilos for r in obj.retiros.all()), Decimal('0')))

    def get_aviso_cartera(self, obj):
        from cartera.models import VerificacionVenta
        from cartera.ventas import verificacion_vigente
        verificacion = verificacion_vigente(obj)
        if verificacion is None or verificacion.estado != VerificacionVenta.DIFERENCIA:
            return None
        return {
            'comentario': verificacion.comentario,
            'usuario': verificacion.usuario.username,
            'fecha': verificacion.fecha,
        }

    def get_lote_proveedor(self, obj) -> str:
        return obj.entrada_detalle.lote_proveedor if obj.entrada_detalle_id else ''

    def get_proveedor_nombre(self, obj) -> str:
        proveedor = obj.entrada_detalle.proveedor_origen if obj.entrada_detalle_id else None
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
    # En un retiro de resguardo: la venta cuyo resguardo entrega.
    venta_resguardo = serializers.SerializerMethodField()

    class Meta:
        model = Salida
        fields = [
            'id', 'folio_de_salida', 'cliente', 'cliente_id', 'fecha', 'notas', 'detalles', 'creado_por',
            'folio_retiro', 'venta_resguardo', 'es_resguardo',
        ]
        read_only_fields = ['folio_retiro']

    def get_venta_resguardo(self, obj):
        if not obj.folio_retiro:
            return None
        lineas = [l for l in obj.detalles.all() if l.retiro_de_id]
        if not lineas:
            return None
        venta = lineas[0].retiro_de.salida
        return {'id': venta.id, 'folio_de_salida': venta.folio_de_salida, 'notas': venta.notas, 'referencia': venta.referencia}

    def validate_folio_de_salida(self, value):
        # Vacío se guarda como NULL: el folio es único, pero puede faltar en varias ventas.
        return _mayusculas(value) or None

    def validate_notas(self, value):
        return _mayusculas(value)

    def validate(self, data):
        detalles = data.get('detalles')
        if not detalles:
            raise serializers.ValidationError({'detalles': 'Debe incluir al menos una línea.'})
        # El tipo no cambia al editar: una venta en resguardo sigue siéndolo.
        es_resguardo = self.instance.es_resguardo if self.instance else data.get('es_resguardo', False)
        data['es_resguardo'] = es_resguardo
        folio = data.get('folio_de_salida', getattr(self.instance, 'folio_de_salida', None))
        if es_resguardo:
            # Por producto, sin lote y sin salida de cámara: todo queda en resguardo.
            data['folio_de_salida'] = None
            if data.get('cliente', getattr(self.instance, 'cliente', None)) is None:
                raise serializers.ValidationError({'cliente_id': 'Una venta en resguardo es a un cliente.'})
            for item in detalles:
                item['entrada_detalle'] = None
                item['cajas_resguardo'], item['kilos_resguardo'] = item['cajas'], item['total_kilos']
                if item['total_kilos'] <= 0:
                    raise serializers.ValidationError({'detalles': f"Captura los kilos de {item['producto']}."})
        else:
            if not folio:
                raise serializers.ValidationError({'folio_de_salida': 'El folio de salida es obligatorio.'})
            # Único entre salidas normales (un retiro puede compartirlo: ver Salida.folio_de_salida).
            otras = Salida.objects.filter(folio_de_salida=folio, folio_retiro__isnull=True)
            if self.instance:
                otras = otras.exclude(pk=self.instance.pk)
            if otras.exists():
                raise serializers.ValidationError({'folio_de_salida': 'Ya existe una salida con ese folio.'})
            for item in detalles:
                if not item.get('entrada_detalle'):
                    raise serializers.ValidationError({'detalles': 'Cada producto debe salir de un lote.'})
                item['cajas_resguardo'], item['kilos_resguardo'] = 0, Decimal('0')
        if self.instance and self.instance.folio_retiro:
            raise serializers.ValidationError({
                'detalles': 'Es un retiro de resguardo: se cancela y se vuelve a registrar desde Resguardos.'
            })

        self._validar_resguardo(data, detalles)

        # Al editar, lo que esta misma Salida ya tenía reservado de un lote se
        # "libera" antes de comparar, porque se va a reemplazar por lo que viene
        # en el payload — igual para cajas que para kilos. Del lote solo sale lo
        # entregado: lo que queda en resguardo sigue en la cámara.
        cajas_ya_reservadas = {}
        kilos_ya_reservados = {}
        if self.instance:
            for linea in self.instance.detalles.exclude(entrada_detalle=None):
                cajas_ya_reservadas[linea.entrada_detalle_id] = (
                    cajas_ya_reservadas.get(linea.entrada_detalle_id, 0) + linea.cajas - linea.cajas_resguardo
                )
                kilos_ya_reservados[linea.entrada_detalle_id] = (
                    kilos_ya_reservados.get(linea.entrada_detalle_id, Decimal('0'))
                    + linea.total_kilos - linea.kilos_resguardo
                )

        cajas_solicitadas = {}
        kilos_solicitados = {}
        lotes = {}
        for item in detalles:
            lote = item['entrada_detalle']
            if lote is None:
                continue  # venta en resguardo: no sale de ningún lote
            lotes[lote.id] = lote
            cajas_solicitadas[lote.id] = (
                cajas_solicitadas.get(lote.id, 0) + item['cajas'] - item.get('cajas_resguardo', 0)
            )
            kilos_solicitados[lote.id] = (
                kilos_solicitados.get(lote.id, Decimal('0'))
                + item['total_kilos'] - item.get('kilos_resguardo', Decimal('0'))
            )

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

    def _validar_resguardo(self, data, detalles):
        """En una venta en resguardo ya entregada en parte, ninguna línea baja de lo ya retirado."""
        if not self.instance:
            return
        # Una línea cuyo resguardo ya se empezó a entregar no se quita, no cambia
        # de producto ni puede quedar con menos resguardo que lo ya retirado.
        enviados = {item['id']: item for item in detalles if item.get('id')}
        for linea in self.instance.detalles.filter(retiros__isnull=False).distinct():
            retirado = sum((r.total_kilos for r in linea.retiros.all()), Decimal('0'))
            item = enviados.get(linea.id)
            if item is None or item['producto'].id != linea.producto_id or item.get('kilos_resguardo', 0) < retirado:
                raise serializers.ValidationError({
                    'detalles': (
                        f'De {linea.producto} ya se entregaron {retirado} kg del resguardo: la línea no se puede '
                        f'quitar, cambiar de producto ni dejar con menos de {retirado} kg en resguardo.'
                    )
                })

    def create(self, validated_data):
        detalles_data = validated_data.pop('detalles')
        if validated_data.get('es_resguardo'):
            # Las notas de venta siempre son de IMPORTADORA; sin lote no hay de dónde tomarla.
            validated_data['empresa'] = Empresa.objects.filter(nombre='IMPORTADORA').first()
        with transaction.atomic():
            salida = Salida.objects.create(**validated_data)
            for item in detalles_data:
                item.pop('id', None)
                self._datos_del_lote(item)
                SalidaDetalle.objects.create(salida=salida, **item)
        return salida

    def update(self, instance, validated_data):
        from cartera.avisos import avisar_correccion, revisiones_de

        detalles_data = validated_data.pop('detalles')

        with transaction.atomic():
            # Lo que Cartera ya revisó, para avisarle si la corrección lo cambia.
            revisiones = revisiones_de(instance)
            for campo, valor in validated_data.items():
                setattr(instance, campo, valor)
            instance.save()

            ids_enviados = {item['id'] for item in detalles_data if 'id' in item}
            for linea in instance.detalles.all():
                if linea.id not in ids_enviados:
                    linea.delete()

            for item in detalles_data:
                linea_id = item.pop('id', None)
                self._datos_del_lote(item)
                if linea_id:
                    SalidaDetalle.objects.filter(id=linea_id, salida=instance).update(**item)
                else:
                    SalidaDetalle.objects.create(salida=instance, **item)

            avisar_correccion(instance, revisiones, self.context['request'].user)

        return instance

    @staticmethod
    def _factura_del_lote(entrada_detalle):
        return entrada_detalle.documento_origen['factura']

    @classmethod
    def _datos_del_lote(cls, item):
        """Cámara y factura heredadas del lote (vacías en una venta en resguardo)."""
        lote = item['entrada_detalle']
        item['factura_proveedor'] = cls._factura_del_lote(lote) if lote else ''
        item['camara'] = lote.camara if lote else None


class MovimientoCamaraSerializer(serializers.ModelSerializer):
    creado_por = UsuarioCreadorSerializer(read_only=True)
    # Descripción del lote movido, publicada en el propio movimiento: la tabla la
    # resolvía en el navegador recorriendo todas las entradas. Además el lote de
    # origen puede haber quedado sin existencia, así que no se puede leer de la
    # foto de existencias.
    lote_origen = serializers.SerializerMethodField()
    # Recibo con el que la mercancía estaba en la cámara de origen, y el que
    # tiene en destino: el propio de la línea de llegada si ya se capturó
    # (Editar recibo), si no el heredado. `recibo_destino_propio` distingue ambos.
    recibo_origen = serializers.SerializerMethodField()
    recibo_destino = serializers.SerializerMethodField()
    recibo_destino_propio = serializers.SerializerMethodField()
    kilos = serializers.DecimalField(source='salida_detalle.total_kilos', max_digits=15, decimal_places=3, read_only=True)
    proveedor = serializers.SerializerMethodField()
    factura = serializers.SerializerMethodField()
    empresa = serializers.SerializerMethodField()
    fecha_caducidad = serializers.DateField(source='entrada_detalle_destino.fecha_caducidad', read_only=True)
    # Lo que queda HOY en destino de lo que se movió (ya descontadas sus
    # salidas y traslados posteriores).
    cajas_disponibles_destino = serializers.SerializerMethodField()
    kilos_disponibles_destino = serializers.SerializerMethodField()

    class Meta:
        model = MovimientoCamara
        fields = [
            'id', 'entrada_detalle_origen', 'salida_detalle', 'entrada_detalle_destino',
            'camara_origen', 'camara_destino', 'fecha', 'cajas', 'creado_por', 'lote_origen',
            'recibo_origen', 'recibo_destino', 'recibo_destino_propio',
            'kilos', 'proveedor', 'factura', 'empresa', 'fecha_caducidad',
            'cajas_disponibles_destino', 'kilos_disponibles_destino',
        ]

    def get_lote_origen(self, obj) -> str:
        lote = obj.entrada_detalle_origen
        return f"{lote.producto} — lote {lote.lote_proveedor}"

    def get_recibo_origen(self, obj) -> str:
        return obj.entrada_detalle_origen.documento_origen['recibo']

    def get_recibo_destino(self, obj) -> str:
        return obj.entrada_detalle_destino.documento_origen['recibo']

    def get_recibo_destino_propio(self, obj) -> bool:
        return obj.entrada_detalle_destino.lote_general_id is not None

    def get_proveedor(self, obj) -> str:
        lote = obj.entrada_detalle_origen
        return lote.proveedor_origen.nombre if lote.proveedor_origen_id else '—'

    def get_factura(self, obj) -> str:
        return obj.entrada_detalle_origen.documento_origen['factura']

    def get_empresa(self, obj) -> str:
        entrada = obj.entrada_detalle_destino.entrada
        return entrada.empresa.nombre if entrada.empresa_id else '—'

    def _destino(self, obj):
        destino = obj.entrada_detalle_destino
        # El ViewSet anota lo ya consumido del destino; sin la anotación las
        # properties lo agregan con una consulta propia.
        anotado = getattr(obj, 'destino_kilos_vendidos', None)
        if anotado is not None:
            destino.kilos_vendidos_anotados = anotado
        return destino

    def get_cajas_disponibles_destino(self, obj) -> int:
        return self._destino(obj).cajas_disponibles

    def get_kilos_disponibles_destino(self, obj) -> str:
        return str(self._destino(obj).kilos_disponibles)


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
    total_kilos = serializers.DecimalField(max_digits=15, decimal_places=3, min_value=0)
    # Recibo de ingreso en la cámara destino, si ya se tiene al registrar el
    # traslado. Opcional: sin él la mercancía conserva el recibo de origen y se
    # puede capturar después (movimientos-camara/<id>/recibo/).
    recibo_destino = serializers.CharField(required=False, allow_blank=True, max_length=30)

    def validate_recibo_destino(self, value):
        return value.strip().upper()

    def validate(self, data):
        origen = data['entrada_detalle_origen']
        # Un recibo puede agrupar mercancía de varias entradas, pero de una sola cámara.
        recibo = data.get('recibo_destino')
        existente = LoteGeneral.objects.select_related('camara').filter(codigo=recibo).first() if recibo else None
        if existente and existente.camara_id != data['camara_destino'].id:
            raise serializers.ValidationError({
                'recibo_destino': f'El recibo {recibo} ya existe en la cámara {existente.camara.nombre}.'
            })
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
        recibo_destino = validated_data.get('recibo_destino', '')

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
                factura_proveedor=origen.documento_origen['factura'],
            )

            entrada_destino = Entrada.objects.create(
                fecha=fecha, es_traslado=True, creado_por=creado_por,
                # Trasladar de cámara no cambia de dueño: sin esto el lote movido
                # desaparecería del filtro por empresa de Existencias.
                empresa=origen.entrada.empresa,
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
                # Se hereda del lote de origen para no perder la trazabilidad al mover cámaras.
                proveedor_origen=origen.proveedor_origen,
                # La caducidad es del producto físico, no de la "entrada" — viaja con
                # el lote al moverlo de cámara, igual que su costo o su peso por caja.
                fecha_caducidad=origen.fecha_caducidad,
            )
            if recibo_destino:
                # Va en la línea de llegada, igual que el recibo de una entrada
                # normal: el lote de origen conserva el suyo.
                entrada_detalle_destino.lote_general, _ = LoteGeneral.objects.get_or_create(
                    codigo=recibo_destino,
                    defaults={'camara': camara_destino, 'fecha_recibo': fecha, 'creado_por': creado_por},
                )
                entrada_detalle_destino.save(update_fields=['lote_general'])

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
