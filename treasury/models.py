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

class CorteCaja(models.Model):
    fecha = models.DateTimeField(unique=True)
    cerrado = models.BooleanField(default=False)
    fecha_cierre = models.DateTimeField(null=True, blank=True)
    # responsable_apertura = models.ForeignKey(models)
    responsable_apertura = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.PROTECT,
        related_name='cortes_aperturados'
    )
    responsable_cierre = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='cortes_cerrados',
        null=True,
        blank=True
    )

    observaciones = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha']

    def __str__(self):
        return f"CorteCaja {self.fecha} ({'Cerrado' if self.cerrado else 'Abierto'})"

    def clean(self):
        if self.cerrado and not self.fecha_cierre:
            raise ValidationError('Un corte cerrado debe tener fecha de cierre.')
        if self.fecha_cierre and not self.cerrado:
            raise ValidationError('Si existe fecha de cierre, el corte debe estar marcado como cerrado.')

    def close(self, responsable_cierre: str, fecha_cierre=None):
        if self.cerrado:
            raise ValidationError('El corte ya está cerrado.')
        self.cerrado = True
        self.responsable_cierre = responsable_cierre
        # La hora de cierre la define el reloj del servidor (ya en America/Mexico_City),
        # no el cliente: evita depender de la hora/zona horaria del navegador.
        self.fecha_cierre = fecha_cierre or timezone.now()
        self.full_clean()
        self.save()

    @property
    def movimientos(self):
        return MovimientoTesoreria.objects.filter(corte=self)

    @classmethod
    def abrir_nuevo_corte(cls, fecha, responsable_apertura, observaciones=''):
        from django.db import transaction

        if cls.objects.filter(cerrado=False).exists():
            raise ValidationError('Debe cerrar el corte de caja abierto antes de abrir uno nuevo.')

        if not Divisa.objects.filter(activa=True).exists():
            raise ValidationError('Debes registrar al menos una divisa activa antes de abrir un corte de caja.')

        if cls.objects.filter(fecha=fecha).exists():
            raise ValidationError('Ya existe un corte para esa fecha.')

        # ultimo_corte = cls.objects.order_by('-fecha').first()
        ultimo_corte = cls.objects.first()
        monedas_previas = {
            saldo.divisa_id: saldo.saldo_final
            for saldo in ultimo_corte.saldos.all()
        } if ultimo_corte else {}

        with transaction.atomic():

            nuevo_corte = cls.objects.create(
                fecha=fecha,
                responsable_apertura=responsable_apertura,
                observaciones=observaciones,
            )

            divisas = list(Divisa.objects.filter(activa=True))
            if ultimo_corte:
                divisas = list({*divisas, *[saldo.divisa for saldo in ultimo_corte.saldos.all()]})

            for divisa in divisas:
                SaldoCaja.objects.create(
                    corte=nuevo_corte,
                    divisa=divisa,
                    saldo_inicial=monedas_previas.get(divisa.id, Decimal('0')),
                    saldo_final=monedas_previas.get(divisa.id, Decimal('0')),
                )
        return nuevo_corte
    
    @classmethod
    def abierto(cls):
        return cls.objects.filter(cerrado=False).first()


    @classmethod
    def ultimo(cls):
        return cls.objects.first()


class SaldoCaja(models.Model):
    corte = models.ForeignKey(
        CorteCaja,
        on_delete=models.CASCADE,
        related_name="saldos"
    )
    divisa = models.ForeignKey(
        Divisa,
        on_delete=models.PROTECT
    )
    saldo_inicial = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    saldo_final = models.DecimalField(max_digits=18, decimal_places=2, default=0, db_column='saldo')
    saldo_fisico = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    diferencia = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    class Meta:
        unique_together = ("corte", "divisa")

    def __str__(self):
        return f"SaldoCaja {self.divisa.codigo} - {self.corte.fecha}"

    @property
    def saldo(self):
        return self.saldo_final

    def tiene_saldo(self, cantidad):
        return self.saldo_disponible >= cantidad

    def _movimientos_por_divisa(self):
        return MovimientoDivisa.objects.filter(
            movimiento__corte=self.corte,
            movimiento__cancelado=False,
            divisa=self.divisa,
        ).select_related('movimiento')

    @property
    def ingresos(self):
        return sum(
            linea.cantidad for linea in self._movimientos_por_divisa()
            if linea.movimiento.tipo == MovimientoTesoreria.INGRESO
        )

    @property
    def egresos(self):
        return sum(
            linea.cantidad for linea in self._movimientos_por_divisa()
            if linea.movimiento.tipo == MovimientoTesoreria.EGRESO
        )

    @property
    def saldo_disponible(self):
        return self.saldo_inicial + self.ingresos - self.egresos

    def actualizar_balance(self):
        self.saldo_final = self.saldo_disponible
        self.save()

    @property
    def saldo_esperado(self):
        return self.saldo_disponible

    def registrar_saldo_fisico(self, saldo_fisico):
        self.saldo_fisico = saldo_fisico
        self.diferencia = saldo_fisico - self.saldo_esperado
        self.save()


class MovimientoTesoreria(models.Model):
    INGRESO = "I"
    EGRESO = "E"

    TIPO_CHOICES = (
        (INGRESO, "Ingreso"),
        (EGRESO, "Egreso"),
    )

    corte = models.ForeignKey(
        CorteCaja,
        on_delete=models.PROTECT,
        related_name='movimientos',
        null=True,
        blank=True,
    )
    fecha = models.DateTimeField()
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

    def __str__(self):
        return f"{self.get_tipo_display()} {self.folio} ({self.fecha})"

    def clean(self):
        if self.corte and self.corte.fecha != self.fecha:
            raise ValidationError('La fecha del movimiento debe coincidir con la fecha del corte asociado.')
        if self.corte and self.corte.cerrado:
            raise ValidationError('No se pueden registrar ni modificar movimientos de un corte cerrado.')

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
        if self.corte and self.corte.cerrado:
            raise ValidationError('No se puede cancelar un movimiento de un corte cerrado.')
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
        if self.corte:
            divisas = {linea.divisa for linea in self.divisas.all()}
            for divisa in divisas:
                saldo, _ = SaldoCaja.objects.get_or_create(
                    corte=self.corte,
                    divisa=divisa,
                    defaults={
                        'saldo_inicial': Decimal('0'),
                        'saldo_final': Decimal('0'),
                    }
                )
                saldo.actualizar_balance()


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
        self.full_clean()
        super().save(*args, **kwargs)
        if self.movimiento and self.movimiento.corte:
            saldo, _ = SaldoCaja.objects.get_or_create(
                corte=self.movimiento.corte,
                divisa=self.divisa,
                defaults={
                    'saldo_inicial': Decimal('0'),
                    'saldo_final': Decimal('0'),
                }
            )
            saldo.actualizar_balance()

    def delete(self, *args, **kwargs):
        corte = self.movimiento.corte if self.movimiento else None
        divisa = self.divisa
        super().delete(*args, **kwargs)
        if corte:
            try:
                saldo = SaldoCaja.objects.get(corte=corte, divisa=divisa)
            except SaldoCaja.DoesNotExist:
                return
            saldo.actualizar_balance()


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
    # unique=True: un solo arqueo por corte (el arqueo vive y se cierra junto con su corte).
    corte = models.ForeignKey(
        CorteCaja,
        on_delete=models.PROTECT,
        related_name='arqueos',
        unique=True,
    )
    hora_inicio = models.DateTimeField()
    hora_termino = models.DateTimeField()
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='arqueos_realizados',
    )
    observaciones = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-hora_termino']

    def __str__(self):
        return f"Arqueo #{self.pk} ({self.hora_termino})"

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
