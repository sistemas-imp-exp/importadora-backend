from decimal import Decimal
from functools import cached_property

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Coalesce


def kilos_salidos(relacion='salidas_detalle', **filtro):
    """
    Kilos que salieron físicamente de un lote por `relacion` (las líneas de salida):
    lo vendido menos lo que se quedó en resguardo del cliente, que sigue en la
    cámara hasta que se retira (y el retiro es otra salida, de cualquier lote).
    `filtro` son condiciones Q sobre esas líneas (p. ej. hasta una fecha).
    """
    return Coalesce(
        models.Sum(
            models.F(f'{relacion}__total_kilos') - models.F(f'{relacion}__kilos_resguardo'),
            filter=models.Q(**filtro) if filtro else None,
        ),
        models.Value(Decimal('0')),
        output_field=models.DecimalField(max_digits=15, decimal_places=3),
    )


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
    """
    Documento de entrada: fecha, empresa y tipo de compra. Proveedor, factura y
    pedimento van en cada línea (una entrada puede traer mercancía de varios
    proveedores) y el recibo de ingreso (LoteGeneral) puede abarcar varias entradas.
    """
    fecha = models.DateField()
    # La mitad "de llegada" de un MovimientoCamara: no es una compra, sus
    # líneas heredan proveedor y documentos del lote de origen.
    es_traslado = models.BooleanField(default=False)
    # A qué empresa del grupo pertenece la mercancía (IMPORTADORA, MARISCOS
    # SELECTOS…). Nullable solo a nivel base de datos: la API la exige en las
    # entradas capturadas y los traslados la heredan de la entrada de origen.
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.PROTECT,
        related_name='entradas',
        null=True,
        blank=True,
    )
    es_internacional = models.BooleanField(default=False)
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
        tipo = "Movimiento interno" if self.es_traslado else "Compra"
        return f"Entrada {self.id} - {tipo} ({self.fecha})"


class LoteGeneral(models.Model):
    """
    Recibo de ingreso (IMP) a una cámara. Agrupa líneas de una o varias
    entradas, todas de la misma cámara; se liga por EntradaDetalle.lote_general.
    """
    codigo = models.CharField(max_length=30, unique=True)
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
            'entrada',
            'movimiento_camara_como_destino__entrada_detalle_origen__entrada',
            'movimiento_camara_como_destino__entrada_detalle_origen__lote_general',
        ).annotate(kilos_vendidos_anotados=kilos_salidos())


class EntradaDetalle(models.Model):
    entrada = models.ForeignKey(
        Entrada, on_delete=models.CASCADE, related_name='detalles'
    )
    producto = models.ForeignKey(
        Producto, on_delete=models.PROTECT, related_name='entradas_detalle'
    )
    # Proveedor del lote: lo captura el usuario en cada línea de la entrada. En la
    # mitad "de llegada" de un MovimientoCamara se hereda del lote de origen para
    # no perder la trazabilidad al mover mercancía entre cámaras.
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
    # Documentos del proveedor de esta línea (vacíos en las líneas de llegada de
    # un traslado: se leen del lote raíz, ver documento_origen).
    factura = models.CharField(max_length=50, blank=True)
    pedimento = models.CharField(max_length=50, blank=True)
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
    # Kilos con 3 decimales (cajas de 20 lb = 9.072 kg); precios por kilo con
    # 4 (salen de convertir USD/lb). Los totales en pesos se quedan en centavos.
    peso_por_caja = models.DecimalField(
        max_digits=10, decimal_places=3, validators=[MinValueValidator(Decimal('0.001'))]
    )
    total_kilos = models.DecimalField(max_digits=15, decimal_places=3)
    costo_por_kilo = models.DecimalField(
        max_digits=14, decimal_places=4, null=True, blank=True
    )
    precio_venta_planeado = models.DecimalField(
        max_digits=14, decimal_places=4, null=True, blank=True
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

        El recibo es la excepción: al llegar a otra cámara la mercancía puede
        recibir un recibo nuevo (Movimientos → Editar recibo), que se guarda en
        la propia línea de llegada. Gana el recibo más cercano en la cadena; si
        ninguno de los traslados tiene uno propio, se hereda el de la compra.
        """
        lote = self
        recibo = self.lote_general.codigo if self.lote_general_id else ''
        visitados = set()
        while lote.entrada.es_traslado:
            movimiento = getattr(lote, 'movimiento_camara_como_destino', None)
            if movimiento is None or movimiento.entrada_detalle_origen_id in visitados:
                break
            visitados.add(lote.id)
            lote = movimiento.entrada_detalle_origen
            if not recibo and lote.lote_general_id:
                recibo = lote.lote_general.codigo

        return {'factura': lote.factura, 'recibo': recibo}

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
            vendidos = EntradaDetalle.objects.filter(pk=self.pk).aggregate(total=kilos_salidos())['total']
        return self.total_kilos - vendidos


class Salida(models.Model):
    # Folio de la salida de cámara. Vacío solo en una venta en resguardo: no
    # sale nada de cámara (lo que se entregue después lleva el folio de su retiro).
    # Único entre salidas normales; un retiro puede repetirlo, porque una misma
    # salida en papel lleva venta nueva y entrega de resguardo del mismo cliente.
    folio_de_salida = models.CharField(max_length=30, null=True, blank=True)
    # Venta en resguardo: el cliente compra producto (por producto, sin lote) que
    # se queda guardado; se cobra completo y se entrega con retiros, del lote que
    # haya. Sus líneas no tienen lote ni descuentan existencias.
    es_resguardo = models.BooleanField(default=False)
    # Empresa de la venta cuando no hay lote del que tomarla (venta en resguardo:
    # las notas siempre son de IMPORTADORA). En una venta normal sale del lote.
    empresa = models.ForeignKey(
        Empresa, on_delete=models.PROTECT, related_name='salidas', null=True, blank=True,
    )
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
    # Folio propio de un retiro de resguardo (RES-0001): la salida con la que el
    # cliente se lleva producto que dejó guardado al comprar. Vacío en las ventas.
    folio_retiro = models.CharField(max_length=20, unique=True, null=True, blank=True)
    # Retiro armado con líneas que ya estaban capturadas en una salida normal
    # ("Ligar entrega ya capturada"): al cancelarlo regresan a esa salida.
    entrega_ligada = models.BooleanField(default=False)
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
        indexes = [
            models.Index(fields=['-fecha'], name='salida_fecha_idx'),
            models.Index(fields=['folio_de_salida'], name='salida_folio_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['folio_de_salida'], condition=models.Q(folio_retiro__isnull=True),
                name='salida_folio_unico_sin_retiros',
            ),
        ]

    def __str__(self):
        return self.referencia

    @property
    def referencia(self):
        """Cómo nombrar la salida en textos: su folio, o su nota si es venta en resguardo."""
        return self.folio_de_salida or f'Nota {self.notas} (venta en resguardo)'

    @property
    def clave_busqueda(self):
        """Lo que encuentra esta salida en los buscadores (enlaces de los avisos)."""
        return self.folio_de_salida or self.notas

    @property
    def es_retiro(self):
        return bool(self.folio_retiro)


class SalidaDetalle(models.Model):
    salida = models.ForeignKey(
        Salida, on_delete=models.CASCADE, related_name='detalles'
    )
    producto = models.ForeignKey(
        Producto, on_delete=models.PROTECT, related_name='salidas_detalle'
    )
    # Lote del que sale. Vacío en una venta en resguardo (se vende por producto).
    entrada_detalle = models.ForeignKey(
        EntradaDetalle, on_delete=models.PROTECT, related_name='salidas_detalle', null=True, blank=True,
    )
    camara = models.ForeignKey(
        Camara,
        on_delete=models.PROTECT,
        related_name='salidas_detalle',
        null=True,
        blank=True,
    )
    cajas = models.PositiveIntegerField()
    total_kilos = models.DecimalField(max_digits=15, decimal_places=3)
    factura_proveedor = models.CharField(max_length=50, blank=True)
    precio_x_kilo = models.DecimalField(
        max_digits=14, decimal_places=4, null=True, blank=True
    )
    total_venta = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    notas = models.CharField(max_length=200, blank=True)
    # Resguardo: lo vendido que el cliente deja guardado (en una venta en
    # resguardo, todo: cajas/kilos = los de la línea). Se cobra completo y se
    # entrega después con retiros, del lote que haya de ese producto.
    cajas_resguardo = models.PositiveIntegerField(default=0)
    kilos_resguardo = models.DecimalField(max_digits=15, decimal_places=3, default=Decimal('0'))
    # En un retiro: la línea de venta cuyo resguardo entrega (mismo producto).
    retiro_de = models.ForeignKey(
        'self', on_delete=models.PROTECT, related_name='retiros', null=True, blank=True,
    )
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-salida__fecha']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kilos_resguardo__gte=0) & models.Q(kilos_resguardo__lte=models.F('total_kilos')),
                name='salidadetalle_resguardo_dentro_de_la_venta',
            ),
        ]

    def __str__(self):
        return f"{self.salida.referencia} - {self.producto}"


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
