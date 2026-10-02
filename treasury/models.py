from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone

from django.conf import settings # Importante para referenciar al modelo User

class Divisa(models.Model):
    codigo = models.CharField(max_length=10, unique=True)
    nombre = models.CharField(max_length=50)
    simbolo = models.CharField(max_length=5)
    activa = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.codigo


class SaldoInicial(models.Model):
    """
    Saldo de caja de una divisa antes de cualquier movimiento, sin fecha: se
    suma a todo cálculo de saldos (treasury/saldos.py). Solo lo fija el superusuario.
    """
    divisa = models.OneToOneField(Divisa, on_delete=models.PROTECT, related_name='saldo_inicial')
    monto = models.DecimalField(max_digits=18, decimal_places=2)
    editado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='saldos_iniciales_editados',
    )
    editado_en = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Saldo inicial {self.divisa.codigo} {self.monto}"


class MovimientoTesoreria(models.Model):
    INGRESO = "I"
    EGRESO = "E"

    TIPO_CHOICES = (
        (INGRESO, "Ingreso"),
        (EGRESO, "Egreso"),
    )

    # Día de la hoja física del movimiento (puede ser anterior a la captura).
    fecha = models.DateField()
    folio = models.CharField(max_length=20, unique=True)
    tipo = models.CharField(max_length=1, choices=TIPO_CHOICES)

    autorizo = models.CharField(max_length=150)
    beneficiario = models.CharField(max_length=200)
    concepto = models.TextField()

    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="movimientos_tesoreria"
    )

    editado = models.BooleanField(default=False)
    # Última edición: siempre el usuario con sesión, nunca un dato del cliente.
    editado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='movimientos_editados',
        null=True,
        blank=True,
    )
    editado_en = models.DateTimeField(null=True, blank=True)

    cancelado = models.BooleanField(default=False)
    fecha_cancelacion = models.DateTimeField(null=True, blank=True)
    usuario_cancelacion = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='movimientos_cancelados',
        null=True,
        blank=True,
    )
    motivo_cancelacion = models.TextField(blank=True)

    class Meta:
        ordering = ['-fecha', '-creado']
        indexes = [models.Index(fields=['-fecha', '-creado'], name='movtesoreria_fecha_idx')]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.folio} ({self.fecha})"

    def clean(self):
        if self.fecha and self.fecha > timezone.localdate():
            raise ValidationError({'fecha': 'La fecha del movimiento no puede ser futura.'})

        if self.pk:
            lineas = self.divisas.all()
        else:
            lineas = []

        if self.tipo in {self.INGRESO, self.EGRESO}:
            for linea in lineas:
                if linea.cantidad <= 0:
                    raise ValidationError('Ingresos y egresos deben tener cantidad positiva en cada divisa.')

    def cancelar(self, usuario, motivo):
        if self.cancelado:
            raise ValidationError('Este movimiento ya está cancelado.')
        if not motivo or not motivo.strip():
            raise ValidationError('Debes indicar el motivo de la cancelación.')

        self.cancelado = True
        self.usuario_cancelacion = usuario
        self.motivo_cancelacion = motivo.strip()
        self.fecha_cancelacion = timezone.now()
        self.save()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ConfiguracionFolio(models.Model):
    tipo = models.CharField(max_length=1, choices=MovimientoTesoreria.TIPO_CHOICES, unique=True)
    siguiente_folio = models.PositiveIntegerField(default=1)

    class Meta:
        verbose_name = 'Configuración de folio'
        verbose_name_plural = 'Configuración de folios'

    def __str__(self):
        return f"{self.get_tipo_display()} - siguiente: {self.siguiente_folio}"


class MovimientoDivisa(models.Model):
    movimiento = models.ForeignKey(
        MovimientoTesoreria,
        on_delete=models.CASCADE,
        related_name="divisas"
    )
    divisa = models.ForeignKey(
        Divisa,
        on_delete=models.PROTECT
    )
    cantidad = models.DecimalField(
        max_digits=18,
        decimal_places=2
    )
    tipo_cambio = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
        help_text='Tipo de cambio aplicado al momento del movimiento.',
    )
    equivalente_mxn = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='Valor histórico en pesos.',
    )

    class Meta:
        unique_together = ("movimiento", "divisa")

    def __str__(self):
        return f"{self.divisa.codigo}: {self.cantidad}"

    def clean(self):
        if self.cantidad == 0:
            raise ValidationError('La cantidad del movimiento de divisa no puede ser cero.')
        if self.movimiento_id and self.movimiento.tipo in {MovimientoTesoreria.INGRESO, MovimientoTesoreria.EGRESO}:
            if self.cantidad < 0:
                raise ValidationError('Ingresos y egresos deben registrar cantidades positivas.')

    def save(self, *args, **kwargs):
        # Los saldos ya no se guardan: se calculan por fecha (treasury/saldos.py).
        self.full_clean()
        super().save(*args, **kwargs)


def ruta_adjunto_movimiento(instance, filename):
    return f"tesoreria/movimientos/{instance.movimiento_id}/{filename}"


class MovimientoArchivo(models.Model):
    EXTENSIONES_PERMITIDAS = ['png', 'jpg', 'jpeg', 'xlsx', 'pdf']
    TAMANO_MAXIMO_BYTES = 10 * 1024 * 1024  # 10 MB

    movimiento = models.ForeignKey(
        MovimientoTesoreria,
        on_delete=models.CASCADE,
        related_name='archivos',
    )
    archivo = models.FileField(
        upload_to=ruta_adjunto_movimiento,
        validators=[FileExtensionValidator(
            allowed_extensions=EXTENSIONES_PERMITIDAS,
            message="No se permite el tipo de archivo “%(extension)s”. Solo se aceptan: %(allowed_extensions)s.",
        )],
    )
    nombre_original = models.CharField(max_length=255)
    tipo_contenido = models.CharField(max_length=100, blank=True)
    tamano = models.PositiveIntegerField()
    subido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='archivos_subidos',
    )
    subido_en = models.DateTimeField(auto_now_add=True)
    # Borrar un adjunto no lo elimina: queda registrado quién y cuándo lo quitó.
    eliminado = models.BooleanField(default=False)
    eliminado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='archivos_movimiento_eliminados',
        null=True,
        blank=True,
    )
    eliminado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-subido_en']

    def __str__(self):
        return f"{self.nombre_original} ({self.movimiento.folio})"

    def clean(self):
        if self.tamano and self.tamano > self.TAMANO_MAXIMO_BYTES:
            limite_mb = self.TAMANO_MAXIMO_BYTES // (1024 * 1024)
            raise ValidationError(f"El archivo no puede pesar más de {limite_mb} MB.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Denominacion(models.Model):
    BILLETE = 'B'
    MONEDA = 'M'

    TIPO_CHOICES = (
        (BILLETE, 'Billete'),
        (MONEDA, 'Moneda'),
    )

    divisa = models.ForeignKey(
        Divisa,
        on_delete=models.CASCADE,
        related_name='denominaciones',
    )
    valor = models.DecimalField(max_digits=18, decimal_places=2)
    tipo = models.CharField(max_length=1, choices=TIPO_CHOICES)
    activa = models.BooleanField(default=True)

    class Meta:
        # (divisa, valor) no basta: México tiene billete Y moneda de 20 pesos,
        # así que el tipo también forma parte de la identidad de la denominación.
        unique_together = ('divisa', 'valor', 'tipo')
        ordering = ['divisa__codigo', '-valor']

    def __str__(self):
        return f"{self.divisa.codigo} {self.valor}"


class ArqueoCaja(models.Model):
    """
    Conteo físico de caja. Se puede hacer en cualquier momento y varias veces
    al día. El resultado esperado de cada divisa es el saldo calculado al
    cierre de `fecha` en el momento del arqueo, y se guarda como foto: si
    después se capturan hojas atrasadas de ese día, el arqueo conserva lo que
    el sistema decía cuando se contó.
    """
    fecha = models.DateField()
    hora_inicio = models.DateTimeField()
    hora_termino = models.DateTimeField()
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='arqueos_realizados',
    )
    editado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='arqueos_editados',
        null=True,
        blank=True,
    )
    editado_en = models.DateTimeField(null=True, blank=True)
    observaciones = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha', '-hora_termino']

    def __str__(self):
        return f"Arqueo #{self.pk} ({self.fecha:%d/%m/%Y})"

    def clean(self):
        if self.hora_inicio and self.hora_termino and self.hora_termino < self.hora_inicio:
            raise ValidationError('La hora de término no puede ser anterior a la hora de inicio.')

    @property
    def leyenda_totales(self):
        partes = [
            f"{linea.divisa.simbolo}{linea.total_contado:,.2f} {linea.divisa.codigo}"
            for linea in self.divisas.select_related('divisa').order_by('divisa__codigo')
        ]
        if not partes:
            return ''
        return (
            "Se finaliza el presente arqueo de caja con un total de: "
            + " + ".join(partes)
            + " pasando a firmar en señal de conformidad."
        )


class ArqueoDivisa(models.Model):
    arqueo = models.ForeignKey(
        ArqueoCaja,
        on_delete=models.CASCADE,
        related_name='divisas',
    )
    divisa = models.ForeignKey(Divisa, on_delete=models.PROTECT)
    saldo_inicial = models.DecimalField(max_digits=18, decimal_places=2)
    resultado_esperado = models.DecimalField(max_digits=18, decimal_places=2)
    total_contado = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    diferencia = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        unique_together = ('arqueo', 'divisa')

    def __str__(self):
        return f"{self.divisa.codigo} - Arqueo #{self.arqueo_id}"

    @property
    def estado(self):
        if self.diferencia > 0:
            return 'SOBRANTE'
        if self.diferencia < 0:
            return 'FALTANTE'
        return 'EXACTO'

    def recalcular_total(self):
        self.total_contado = sum(
            (conteo.total for conteo in self.conteos.select_related('denominacion')),
            Decimal('0'),
        )
        self.diferencia = self.total_contado - self.resultado_esperado
        self.save()


class ArqueoConteo(models.Model):
    arqueo_divisa = models.ForeignKey(
        ArqueoDivisa,
        on_delete=models.CASCADE,
        related_name='conteos',
    )
    denominacion = models.ForeignKey(Denominacion, on_delete=models.PROTECT)
    piezas = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('arqueo_divisa', 'denominacion')

    def __str__(self):
        return f"{self.denominacion} x{self.piezas}"

    @property
    def total(self):
        return self.piezas * self.denominacion.valor

    def clean(self):
        if self.denominacion_id and self.arqueo_divisa_id and self.denominacion.divisa_id != self.arqueo_divisa.divisa_id:
            raise ValidationError('La denominación no corresponde a la divisa de este arqueo.')


class TipoCambio(models.Model):
    fecha = models.DateField()
    divisa = models.ForeignKey(
        Divisa,
        related_name='tipos_cambio',
        on_delete=models.PROTECT
    )
    valor_mxn = models.DecimalField(max_digits=18, decimal_places=6)

    class Meta:
        unique_together = ("fecha", "divisa")
        ordering = ['-fecha']

    def __str__(self):
        return f"{self.divisa.codigo} {self.fecha}: {self.valor_mxn}"


class Rancho(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class Puesto(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class Banco(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class Empleado(models.Model):
    rancho = models.ForeignKey(
        Rancho,
        on_delete=models.PROTECT,
        related_name='empleados',
    )
    puesto = models.ForeignKey(
        Puesto,
        on_delete=models.PROTECT,
        related_name='empleados',
    )
    nombre = models.CharField(max_length=150)
    salario_diario = models.DecimalField(max_digits=10, decimal_places=2)
    numero_cuenta = models.CharField(max_length=30, blank=True)
    banco = models.ForeignKey(
        Banco,
        on_delete=models.PROTECT,
        related_name='empleados',
        null=True,
        blank=True,
    )
    nombre_cuenta = models.CharField(max_length=150, blank=True)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['rancho__nombre', 'nombre']

    def __str__(self):
        return f"{self.nombre} ({self.rancho.nombre})"


class NominaSemanal(models.Model):
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()

    cerrada = models.BooleanField(default=False)
    fecha_cierre = models.DateTimeField(null=True, blank=True)
    cerrada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='nominas_cerradas',
        null=True,
        blank=True,
    )

    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='nominas_creadas',
    )
    observaciones = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha_inicio']

    def __str__(self):
        return f"Nómina {self.fecha_inicio} - {self.fecha_fin}"

    def clean(self):
        if self.fecha_inicio and self.fecha_fin and self.fecha_fin < self.fecha_inicio:
            raise ValidationError('La fecha de fin no puede ser anterior a la fecha de inicio.')

    def close(self, usuario):
        if self.cerrada:
            raise ValidationError('Esta nómina ya está cerrada.')
        self.cerrada = True
        self.cerrada_por = usuario
        self.fecha_cierre = timezone.now()
        self.full_clean()
        self.save()

    @property
    def total_bruto(self):
        return sum((detalle.total_bruto for detalle in self.detalles.all()), Decimal('0'))

    @property
    def total_neto(self):
        return sum((detalle.total_neto for detalle in self.detalles.all()), Decimal('0'))


class NominaDetalle(models.Model):
    nomina = models.ForeignKey(
        NominaSemanal,
        on_delete=models.CASCADE,
        related_name='detalles',
    )
    empleado = models.ForeignKey(
        Empleado,
        on_delete=models.PROTECT,
        related_name='nomina_detalles',
    )
    dias_trabajados = models.DecimalField(max_digits=3, decimal_places=1, default=0)
    salario_diario = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text='Salario diario del empleado al momento de capturar esta nómina.',
    )
    descuento = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        unique_together = ('nomina', 'empleado')

    def __str__(self):
        return f"{self.empleado.nombre} - Nómina #{self.nomina_id}"

    @property
    def total_bruto(self):
        return self.dias_trabajados * self.salario_diario

    @property
    def total_neto(self):
        return self.total_bruto - self.descuento

    def clean(self):
        if self.dias_trabajados is not None and not (Decimal('0') <= self.dias_trabajados <= Decimal('7')):
            raise ValidationError('Los días trabajados deben estar entre 0 y 7.')
        if self.descuento is not None and self.descuento < 0:
            raise ValidationError('El descuento no puede ser negativo.')
        if self.descuento is not None and self.descuento > self.total_bruto:
            raise ValidationError('El descuento no puede ser mayor al total bruto del empleado.')
