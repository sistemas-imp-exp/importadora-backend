from django.db import models


class Empresa(models.Model):
    nombre = models.CharField(max_length=150, unique=True)
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
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class Proveedor(models.Model):
    nombre = models.CharField(max_length=150, unique=True)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class Cliente(models.Model):
    nombre = models.CharField(max_length=150, unique=True)
    activo = models.BooleanField(default=True)
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
        Proveedor, on_delete=models.PROTECT, related_name='entradas'
    )
    factura = models.CharField(max_length=50, blank=True)
    pedimento = models.CharField(max_length=50, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha']

    def __str__(self):
        return f"Entrada {self.id} - {self.proveedor.nombre} ({self.fecha})"


class LoteGeneral(models.Model):
    codigo = models.CharField(max_length=30, unique=True)
    entrada = models.ForeignKey(
        Entrada, on_delete=models.PROTECT, related_name='lotes_generales'
    )
    camara = models.ForeignKey(
        Camara, on_delete=models.PROTECT, related_name='lotes_generales'
    )
    fecha_recibo = models.DateField()
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha_recibo']

    def __str__(self):
        return self.codigo


class EntradaDetalle(models.Model):
    entrada = models.ForeignKey(
        Entrada, on_delete=models.PROTECT, related_name='detalles'
    )
    producto = models.ForeignKey(
        Producto, on_delete=models.PROTECT, related_name='entradas_detalle'
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
    peso_por_caja = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )
    total_kilos = models.DecimalField(max_digits=12, decimal_places=2)
    costo_por_kilo = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    precio_venta_planeado = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    observaciones = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-entrada__fecha']
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(lote_proveedor=''),
                name='entradadetalle_lote_proveedor_no_vacio',
            ),
        ]

    def __str__(self):
        return f"{self.producto} - {self.lote_proveedor}"


class Salida(models.Model):
    folio_de_salida = models.CharField(max_length=30, unique=True)
    cliente = models.ForeignKey(
        Cliente, on_delete=models.PROTECT, related_name='salidas'
    )
    fecha = models.DateField()
    notas = models.CharField(max_length=100, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha']

    def __str__(self):
        return self.folio_de_salida


class SalidaDetalle(models.Model):
    salida = models.ForeignKey(
        Salida, on_delete=models.PROTECT, related_name='detalles'
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
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha']

    def __str__(self):
        return f"{self.camara_origen} -> {self.camara_destino} ({self.fecha})"
