from decimal import Decimal
from functools import cached_property

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Coalesce


class Empresa(models.Model):
    nombre = models.CharField(max_length=150, unique=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='empresas_creadas',
        null=True,
        blank=True,
    )
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class Camara(models.Model):
    TIPO_PROPIA = 'propia'
    TIPO_TERCERO = 'tercero'
    TIPO_CHOICES = [
        (TIPO_PROPIA, 'Propia'),
        (TIPO_TERCERO, 'Rentada de tercero'),
    ]

    nombre = models.CharField(max_length=100, unique=True)
    ubicacion = models.CharField(max_length=150, blank=True)
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.PROTECT,
        related_name='camaras',
        null=True,
        blank=True,
    )
    activo = models.BooleanField(default=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='camaras_creadas',
        null=True,
        blank=True,
    )
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class Proveedor(models.Model):
    nombre = models.CharField(max_length=150, unique=True)
    activo = models.BooleanField(default=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='proveedores_creados',
        null=True,
        blank=True,
    )
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class Cliente(models.Model):
    nombre = models.CharField(max_length=150, unique=True)
    activo = models.BooleanField(default=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='clientes_creados',
        null=True,
        blank=True,
    )
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class Producto(models.Model):
    PRESENTACION_ENTERO = 'entero'
    PRESENTACION_COLAS = 'colas'
    PRESENTACION_CHOICES = [
        (PRESENTACION_ENTERO, 'Entero'),
        (PRESENTACION_COLAS, 'Colas'),
    ]

    talla = models.CharField(max_length=50)
    tipo = models.CharField(max_length=50)
    categoria = models.CharField(max_length=50, blank=True)
    presentacion = models.CharField(
        max_length=10, choices=PRESENTACION_CHOICES, blank=True
    )
    activo = models.BooleanField(default=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='productos_creados',
        null=True,
        blank=True,
    )
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['tipo', 'talla']
        constraints = [
            models.UniqueConstraint(fields=['talla', 'tipo'], name='producto_talla_tipo_unico'),
        ]

    def __str__(self):
        return f"{self.talla} {self.tipo}".strip()


class Entrada(models.Model):
    fecha = models.DateField()
    proveedor = models.ForeignKey(
        Proveedor,
        on_delete=models.PROTECT,
        related_name='entradas',
        null=True,
        blank=True,
    )
    es_internacional = models.BooleanField(default=False)
    factura = models.CharField(max_length=50, blank=True)
    pedimento = models.CharField(max_length=50, blank=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='entradas_creadas',
        null=True,
        blank=True,
    )
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha']
        indexes = [models.Index(fields=['-fecha'], name='entrada_fecha_idx')]

    def __str__(self):
        proveedor = self.proveedor.nombre if self.proveedor else "Movimiento interno"
        return f"Entrada {self.id} - {proveedor} ({self.fecha})"


class LoteGeneral(models.Model):
    codigo = models.CharField(max_length=30, unique=True)
    entrada = models.ForeignKey(
        Entrada, on_delete=models.PROTECT, related_name='lotes_generales'
    )
    camara = models.ForeignKey(
        Camara, on_delete=models.PROTECT, related_name='lotes_generales'
    )
    fecha_recibo = models.DateField()
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='lotes_generales_creados',
        null=True,
        blank=True,
    )
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha_recibo']
        indexes = [models.Index(fields=['-fecha_recibo'], name='lotegeneral_fecha_idx')]

    def __str__(self):
        return self.codigo


class EntradaDetalleQuerySet(models.QuerySet):
    def con_consumo(self):
        """
        Anota lo ya vendido/movido de cada lote y trae de una vez todo lo que
        necesitan `cajas_disponibles`, `kilos_disponibles` y `documento_origen`.

        Sin esto, serializar N lotes dispara varias consultas por lote (una
        agregación más el recorrido de la cadena de movimientos): listar las
        entradas costaba más de 9,000 consultas y medio minuto.

        Solo se anota lo vendido en kilos: cajas_disponibles se deriva de ahí
        (ver esa property), no necesita su propia agregación aparte.
        """
        return self.select_related(
            # producto/proveedor se serializan con su `creado_por` anidado, así que
            # el usuario entra en el select_related o vuelve el N+1 por esa vía.
            'producto__creado_por',
            'camara',
            'proveedor_origen__creado_por',
            'lote_general',
            'entrada__proveedor',
            'movimiento_camara_como_destino__entrada_detalle_origen__entrada',
            'movimiento_camara_como_destino__entrada_detalle_origen__lote_general',
        ).annotate(
            kilos_vendidos_anotados=Coalesce(
                models.Sum('salidas_detalle__total_kilos'),
                models.Value(Decimal('0')),
                output_field=models.DecimalField(max_digits=12, decimal_places=2),
            ),
        )


class EntradaDetalle(models.Model):
    entrada = models.ForeignKey(
        Entrada, on_delete=models.CASCADE, related_name='detalles'
    )
    producto = models.ForeignKey(
        Producto, on_delete=models.PROTECT, related_name='entradas_detalle'
    )
    # Proveedor real del lote. En una entrada normal es el mismo que entrada.proveedor;
    # en la mitad "de llegada" de un MovimientoCamara, entrada.proveedor es None (no es
    # una compra real) pero el producto sigue viniendo de este proveedor — se hereda del
    # lote de origen para no perder la trazabilidad al mover mercancía entre cámaras.
    proveedor_origen = models.ForeignKey(
        Proveedor,
        on_delete=models.PROTECT,
        related_name='lotes_de_origen',
        null=True,
        blank=True,
    )
    lote_general = models.ForeignKey(
        LoteGeneral,
        on_delete=models.PROTECT,
        related_name='entradas_detalle',
        null=True,
        blank=True,
    )
    lote_proveedor = models.CharField(max_length=50)
    camara = models.ForeignKey(
        Camara,
        on_delete=models.PROTECT,
        related_name='entradas_detalle',
        null=True,
        blank=True,
    )
    cajas = models.PositiveIntegerField()
    # Obligatorio: es lo que permite derivar cajas_disponibles a partir de
    # kilos_disponibles (ver esa property) sin tener que rastrear cajas y
    # kilos como dos contadores independientes que se pueden desincronizar
    # (p. ej. al vender kilos sueltos de una caja ya abierta). Mayor a cero
    # porque cajas_disponibles divide entre este valor.
    peso_por_caja = models.DecimalField(
        max_digits=8, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))]
    )
    total_kilos = models.DecimalField(max_digits=12, decimal_places=2)
    costo_por_kilo = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    precio_venta_planeado = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    fecha_caducidad = models.DateField(null=True, blank=True)
    observaciones = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    objects = EntradaDetalleQuerySet.as_manager()

    class Meta:
        ordering = ['-entrada__fecha']
        indexes = [
            # Las alertas filtran por ventana de caducidad sobre toda la tabla.
            models.Index(fields=['fecha_caducidad'], name='entdet_caducidad_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(lote_proveedor=''),
                name='entradadetalle_lote_proveedor_no_vacio',
            ),
        ]

    def __str__(self):
        return f"{self.producto} - {self.lote_proveedor}"

    @cached_property
    def documento_origen(self):
        """
        Factura y recibo de ingreso del lote RAÍZ.

        Un lote que llegó por un movimiento entre cámaras vive en una entrada
        "de llegada" sin proveedor, factura ni recibo, así que hay que subir por
        la cadena de movimientos hasta la compra real. Se calcula en vez de
        copiarse para que corregir la factura (Auditoría de entradas) se refleje
        también en los lotes movidos, sin copias que se queden viejas.
        """
        lote = self
        visitados = set()
        while lote.entrada.proveedor_id is None:
            movimiento = getattr(lote, 'movimiento_camara_como_destino', None)
            if movimiento is None or movimiento.entrada_detalle_origen_id in visitados:
                break
            visitados.add(lote.id)
            lote = movimiento.entrada_detalle_origen

        return {
            'factura': lote.entrada.factura,
            'recibo': lote.lote_general.codigo if lote.lote_general_id else '',
        }

    @property
    def cajas_disponibles(self):
        """
        Piso de kilos_disponibles / peso_por_caja, no un contador aparte.

        Antes se restaba `cajas` vendidas de forma independiente a como se
        restaban los kilos, así que los dos números se podían desincronizar
        (p. ej. al vender kilos sueltos de una caja ya abierta, que solo
        movía kilos_disponibles y dejaba cajas_disponibles intacto). Derivar
        cajas de kilos los mantiene siempre consistentes entre sí, y el piso
        (en vez de redondear) evita ofrecer una caja completa que ya no tiene
        peso suficiente.
        """
        return int(self.kilos_disponibles // self.peso_por_caja)

    @property
    def kilos_disponibles(self):
        vendidos = getattr(self, 'kilos_vendidos_anotados', None)
        if vendidos is None:
            vendidos = self.salidas_detalle.aggregate(total=models.Sum('total_kilos'))['total'] or 0
        return self.total_kilos - vendidos


class Salida(models.Model):
    folio_de_salida = models.CharField(max_length=30, unique=True)
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.PROTECT,
        related_name='salidas',
        null=True,
        blank=True,
    )
    fecha = models.DateField()
    # Obligatoria: es el folio de la nota de salida en papel, el documento con
    # el que sale la mercancía. Sin él, una salida del sistema no se puede
    # amarrar con nada físico. Los movimientos entre cámaras la llenan solos
    # ("Movimiento entre cámaras"), y las salidas ya cargadas del Excel se
    # quedan con la suya vacía: blank=False solo valida capturas nuevas.
    notas = models.CharField(max_length=100)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='salidas_creadas',
        null=True,
        blank=True,
    )
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha']
        indexes = [models.Index(fields=['-fecha'], name='salida_fecha_idx')]

    def __str__(self):
        return self.folio_de_salida


class SalidaDetalle(models.Model):
    salida = models.ForeignKey(
        Salida, on_delete=models.CASCADE, related_name='detalles'
    )
    producto = models.ForeignKey(
        Producto, on_delete=models.PROTECT, related_name='salidas_detalle'
    )
    entrada_detalle = models.ForeignKey(
        EntradaDetalle, on_delete=models.PROTECT, related_name='salidas_detalle'
    )
    camara = models.ForeignKey(
        Camara,
        on_delete=models.PROTECT,
        related_name='salidas_detalle',
        null=True,
        blank=True,
    )
    cajas = models.PositiveIntegerField()
    total_kilos = models.DecimalField(max_digits=12, decimal_places=2)
    factura_proveedor = models.CharField(max_length=50, blank=True)
    precio_x_kilo = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    total_venta = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    notas = models.CharField(max_length=200, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-salida__fecha']

    def __str__(self):
        return f"{self.salida.folio_de_salida} - {self.producto}"


class MovimientoCamara(models.Model):
    entrada_detalle_origen = models.ForeignKey(
        EntradaDetalle,
        on_delete=models.PROTECT,
        related_name='movimientos_como_origen',
    )
    salida_detalle = models.OneToOneField(
        SalidaDetalle, on_delete=models.PROTECT, related_name='movimiento_camara'
    )
    entrada_detalle_destino = models.OneToOneField(
        EntradaDetalle,
        on_delete=models.PROTECT,
        related_name='movimiento_camara_como_destino',
    )
    camara_origen = models.ForeignKey(
        Camara, on_delete=models.PROTECT, related_name='movimientos_salida'
    )
    camara_destino = models.ForeignKey(
        Camara, on_delete=models.PROTECT, related_name='movimientos_entrada'
    )
    fecha = models.DateField()
    cajas = models.PositiveIntegerField()
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='movimientos_creados',
        null=True,
        blank=True,
    )
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha']
        indexes = [models.Index(fields=['-fecha'], name='movcamara_fecha_idx')]

    def __str__(self):
        return f"{self.camara_origen} -> {self.camara_destino} ({self.fecha})"


class EdicionEntrada(models.Model):
    """
    Bitácora de ediciones hechas a una entrada por la vía restringida de
    superusuario (ver `inventario/auditoria.py`). Una fila por campo cambiado,
    para que la pregunta "quién movió qué y cuándo" se responda leyendo la tabla.

    Solo se registran aquí los campos que NO alteran la contabilidad del
    inventario: cantidades, cámara, producto y costo nunca pasan por este camino.
    """
    # SET_NULL y no PROTECT: la bitácora no debe impedir borrar lo que audita.
    # Su historia tiene que sobrevivir al registro borrado — justo cuando más
    # importa — así que las referencias legibles se guardan denormalizadas abajo.
    entrada = models.ForeignKey(
        Entrada, on_delete=models.SET_NULL, related_name='ediciones', null=True, blank=True
    )
    entrada_detalle = models.ForeignKey(
        EntradaDetalle,
        on_delete=models.SET_NULL,
        related_name='ediciones',
        null=True,
        blank=True,
        help_text='Nulo cuando el campo editado es de la cabecera, o si la línea ya se borró.',
    )
    # Copia de texto tomada al momento del cambio: mantiene la fila legible
    # aunque la entrada o la línea ya no existan.
    entrada_referencia = models.CharField(max_length=200, blank=True)
    linea_referencia = models.CharField(max_length=200, blank=True)
    campo = models.CharField(max_length=40)
    valor_anterior = models.TextField(blank=True)
    valor_nuevo = models.TextField(blank=True)
    motivo = models.CharField(max_length=200)
    editado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='ediciones_entrada',
        null=True,
        blank=True,
    )
    editado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-editado_en']
        indexes = [models.Index(fields=['-editado_en'], name='edicionentrada_fecha_idx')]
        verbose_name = 'Edición de entrada'
        verbose_name_plural = 'Ediciones de entradas'

    def __str__(self):
        return f"#{self.entrada_id} {self.campo}: {self.valor_anterior} -> {self.valor_nuevo}"
