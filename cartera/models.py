"""
Cartera de clientes: lo que cada cliente debe a cada empresa del grupo.

El saldo de un cliente en una empresa **se calcula** (cartera/saldos.py):
saldo inicial (carga de arranque) + ventas (líneas de salida de Inventario
a ese cliente, posteriores al corte) + cargos − pagos − abonos, sin lo
cancelado. Nada se borra: pagos y ajustes se cancelan con motivo.
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from inventario.models import Cliente, Empresa, SalidaDetalle


class ClienteCartera(models.Model):
    """Datos de cobranza del cliente (el catálogo es el de Inventario)."""
    cliente = models.OneToOneField(Cliente, on_delete=models.CASCADE, primary_key=True, related_name='cartera')
    celular = models.CharField(max_length=120, blank=True)
    pagare = models.BooleanField(default=False, help_text='El cliente firmó pagaré.')
    notas = models.TextField(blank=True)
    # Último pago según el Excel del área al arrancar; los pagos capturados
    # en el sistema lo reemplazan en los reportes.
    pago_previo_fecha = models.DateField(null=True, blank=True)
    pago_previo_monto = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    def __str__(self):
        return self.cliente.nombre


class SaldoInicialCliente(models.Model):
    """Saldo con el que arranca el cliente en una empresa (negativo = saldo a favor)."""
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='saldos_iniciales_cartera')
    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, related_name='saldos_iniciales_cartera')
    monto = models.DecimalField(max_digits=18, decimal_places=2)
    fecha = models.DateField(help_text='Fecha de corte de la carga inicial.')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['cliente', 'empresa'], name='saldo_inicial_cliente_unico')]

    def __str__(self):
        return f"{self.cliente.nombre} · {self.empresa.nombre}: {self.monto}"


class MovimientoCarteraBase(models.Model):
    """Lo común de pagos y ajustes: cliente, empresa, rastro de usuario y cancelación."""
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='+')
    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, related_name='+')
    fecha = models.DateField()
    monto = models.DecimalField(max_digits=18, decimal_places=2)
    notas = models.TextField(blank=True)

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)
    editado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True, blank=True,
    )
    editado_en = models.DateTimeField(null=True, blank=True)

    cancelado = models.BooleanField(default=False)
    fecha_cancelacion = models.DateTimeField(null=True, blank=True)
    usuario_cancelacion = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True, blank=True,
    )
    motivo_cancelacion = models.TextField(blank=True)

    class Meta:
        abstract = True
        ordering = ['-fecha', '-creado']

    def clean(self):
        if self.monto is not None and self.monto <= 0:
            raise ValidationError({'monto': 'El monto debe ser mayor que cero.'})
        if self.fecha and self.fecha > timezone.localdate():
            raise ValidationError({'fecha': 'La fecha no puede ser futura.'})

    def marcar_cancelado(self, usuario, motivo):
        if self.cancelado:
            raise ValidationError('Ya está cancelado.')
        if not motivo or not motivo.strip():
            raise ValidationError('Debes indicar el motivo de la cancelación.')
        self.cancelado = True
        self.usuario_cancelacion = usuario
        self.motivo_cancelacion = motivo.strip()
        self.fecha_cancelacion = timezone.now()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PagoCliente(MovimientoCarteraBase):
    """
    Pago del cliente a una empresa: baja su saldo. Un pago que llega sin saber
    de quién es queda "por identificar" (sin cliente, no mueve ningún saldo)
    hasta que se le asigna cliente; entonces cuenta con su fecha original.
    """
    TRANSFERENCIA = 'T'
    EFECTIVO = 'E'
    EFECTIVO_TESORERIA = 'A'
    EFECTIVO_SUCURSAL = 'AA'
    TIPO_CHOICES = (
        (TRANSFERENCIA, 'Transferencia'),
        (EFECTIVO, 'Efectivo'),
        (EFECTIVO_TESORERIA, 'Efectivo a Tesorería'),
        (EFECTIVO_SUCURSAL, 'Efectivo Arriaga / Mapastepec'),
    )

    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    tipo = models.CharField(max_length=2, choices=TIPO_CHOICES)
    referencia = models.CharField(max_length=120, blank=True)
    # Quién y cuándo asignó el cliente a un pago que llegó por identificar.
    identificado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True, blank=True,
    )
    identificado_en = models.DateTimeField(null=True, blank=True)
    # El efectivo que entró a la caja de Tesorería se toma de ahí (no se
    # recaptura): fecha y monto salen del ingreso, que solo sirve a un pago activo.
    ingreso_tesoreria = models.ForeignKey(
        'treasury.MovimientoTesoreria', on_delete=models.PROTECT, related_name='pagos_cartera',
        null=True, blank=True,
    )

    class Meta(MovimientoCarteraBase.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=['ingreso_tesoreria'], condition=Q(cancelado=False, ingreso_tesoreria__isnull=False),
                name='pago_cartera_ingreso_unico',
            ),
        ]

    def __str__(self):
        cliente = self.cliente.nombre if self.cliente_id else 'POR IDENTIFICAR'
        return f"Pago {self.get_tipo_display()} {cliente} {self.monto}"

    @property
    def por_identificar(self):
        return self.cliente_id is None


class AjusteCliente(MovimientoCarteraBase):
    """
    Movimiento libre que sube (cargo) o baja (abono) el saldo: "pago 2 kg cam.",
    "cargo por horas extras", "modificación de precios", etc. Un traspaso entre
    empresas son dos ajustes ligados por `contraparte` (abono en la de origen,
    cargo en la de destino) que se cancelan juntos.
    """
    CARGO = 'C'
    ABONO = 'A'
    NATURALEZA_CHOICES = ((CARGO, 'Cargo'), (ABONO, 'Abono'))

    naturaleza = models.CharField(max_length=1, choices=NATURALEZA_CHOICES)
    concepto = models.CharField(max_length=255)
    contraparte = models.OneToOneField('self', on_delete=models.PROTECT, null=True, blank=True, related_name='+')

    def __str__(self):
        return f"{self.get_naturaleza_display()} {self.cliente.nombre}: {self.concepto} {self.monto}"

    @property
    def es_traspaso(self):
        return self.contraparte_id is not None

    @property
    def efecto(self):
        """Lo que mueve el saldo: + cargo, − abono."""
        return self.monto if self.naturaleza == self.CARGO else -self.monto


class VerificacionVenta(models.Model):
    """
    Revisión de Cartera de una línea de salida (venta) contra la nota en papel.
    `huella` guarda lo que se revisó (cliente, producto, lote, kilos, precio,
    importe): si Inventario corrige la línea, ya no coincide y la línea vuelve
    a quedar pendiente (ver cartera/ventas.py). Si Inventario la borra al
    editar la salida, la revisión se va con ella.
    """
    VERIFICADA = 'verificada'
    DIFERENCIA = 'diferencia'
    ESTADO_CHOICES = ((VERIFICADA, 'Verificada'), (DIFERENCIA, 'Con diferencia'))

    salida_detalle = models.OneToOneField(
        SalidaDetalle, on_delete=models.CASCADE, primary_key=True, related_name='verificacion_cartera',
    )
    estado = models.CharField(max_length=12, choices=ESTADO_CHOICES)
    comentario = models.TextField(blank=True)
    huella = models.CharField(max_length=255)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    fecha = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.salida_detalle_id}: {self.get_estado_display()}"


CERO = Decimal('0')


class CostoVenta(models.Model):
    """
    Costo unitario ($/kg) que Cartera asigna a una línea de venta para la
    utilidad real, rastreando la factura del producto. Sin captura, la utilidad
    propone el costo del lote. Cada cambio queda en CambioCostoVenta.
    """
    salida_detalle = models.OneToOneField(
        SalidaDetalle, on_delete=models.CASCADE, primary_key=True, related_name='costo_cartera',
    )
    costo_unitario = models.DecimalField(max_digits=14, decimal_places=4, validators=[MinValueValidator(Decimal('0'))])
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    modificado = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.salida_detalle_id}: {self.costo_unitario}"


class CambioCostoVenta(models.Model):
    """Bitácora de los costos capturados: quién, cuándo, antes y después (vacío = propuesto del lote)."""
    salida_detalle = models.ForeignKey(SalidaDetalle, on_delete=models.CASCADE, related_name='cambios_costo_cartera')
    anterior = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    nuevo = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']


class GastoOperativo(models.Model):
    """
    Gasto operativo por kilo vendido que la utilidad real resta a la bruta
    (hoy $4/kg). Vale desde `vigente_desde` hasta el siguiente: cambiarlo no
    recalcula los meses anteriores.
    """
    por_kilo = models.DecimalField(max_digits=10, decimal_places=4, validators=[MinValueValidator(Decimal('0'))])
    vigente_desde = models.DateField(unique=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-vigente_desde']

    def __str__(self):
        return f"${self.por_kilo}/kg desde {self.vigente_desde}"
