"""
Ganado: inventario de cabezas por rancho.

Cada animal nace en el sistema con un movimiento de **alta** (compra,
nacimiento o regalo), se mueve entre ranchos propios con **traspasos** y sale
con una **baja** (venta o muerte). Dónde está y en qué estado se deriva del
último movimiento activo (`servicio.recalcular`); `rancho` y `estado` del
animal son una copia para filtrar rápido. Nada se borra: los movimientos se
cancelan con motivo, solo el último de cada animal.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Rancho(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    activo = models.BooleanField(default=True)
    notas = models.TextField(blank=True)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class Especie(models.Model):
    nombre = models.CharField(max_length=60, unique=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


HEMBRA = 'H'
MACHO = 'M'
SEXOS = [(HEMBRA, 'Hembra'), (MACHO, 'Macho')]


class Categoria(models.Model):
    """Vaca, novilla, becerro, toro, yegua… Con sexo fijo o sin él (aplica a ambos)."""
    especie = models.ForeignKey(Especie, on_delete=models.PROTECT, related_name='categorias')
    nombre = models.CharField(max_length=60)
    sexo = models.CharField(max_length=1, choices=SEXOS, blank=True)
    orden = models.PositiveSmallIntegerField(default=0)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['especie__nombre', 'orden', 'nombre']
        constraints = [
            models.UniqueConstraint(fields=['especie', 'nombre'], name='ganado_categoria_unica'),
        ]

    def __str__(self):
        return self.nombre


ACTIVO = 'ACTIVO'
VENDIDO = 'VENDIDO'
MUERTO = 'MUERTO'
CANCELADO = 'CANCELADO'  # su alta se canceló: no existe para el inventario
ESTADOS = [(ACTIVO, 'Activo'), (VENDIDO, 'Vendido'), (MUERTO, 'Muerto'), (CANCELADO, 'Alta cancelada')]


class Animal(models.Model):
    # Arete en 3 partes como viene en el arete físico (0007 2484 6789); se
    # guardan como texto para conservar los ceros. Sin arete = folio interno.
    folio = models.CharField(max_length=12, unique=True, null=True, editable=False)
    arete_1 = models.CharField(max_length=10, blank=True)
    arete_2 = models.CharField(max_length=10, blank=True)
    arete_3 = models.CharField(max_length=10, blank=True)
    especie = models.ForeignKey(Especie, on_delete=models.PROTECT, related_name='animales')
    categoria = models.ForeignKey(Categoria, on_delete=models.PROTECT, related_name='animales')
    sexo = models.CharField(max_length=1, choices=SEXOS)
    raza = models.CharField(max_length=60, blank=True, help_text='Raza o color.')
    fecha_nacimiento = models.DateField(null=True, blank=True)
    notas = models.TextField(blank=True)
    # Copia del último movimiento activo (servicio.recalcular).
    rancho = models.ForeignKey(Rancho, on_delete=models.PROTECT, related_name='animales', null=True)
    estado = models.CharField(max_length=10, choices=ESTADOS, default=ACTIVO)
    creado = models.DateTimeField(default=timezone.now)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='animales_ganado')

    class Meta:
        ordering = ['id']
        constraints = [
            # Un arete identifica a un solo animal (salvo altas canceladas).
            models.UniqueConstraint(
                fields=['arete_1', 'arete_2', 'arete_3'],
                condition=~Q(arete_3='') & ~Q(estado=CANCELADO),
                name='ganado_arete_unico',
                violation_error_message='Ya hay un animal registrado con ese arete.',
            ),
        ]
        indexes = [models.Index(fields=['arete_3'])]

    @property
    def arete(self):
        return ' '.join(p for p in (self.arete_1, self.arete_2, self.arete_3) if p)

    @property
    def identificacion(self):
        return self.arete or self.folio

    def clean(self):
        for campo in ('arete_1', 'arete_2', 'arete_3'):
            setattr(self, campo, (getattr(self, campo) or '').strip())
        partes = [self.arete_1, self.arete_2, self.arete_3]
        errores = {}
        if any(partes) and not all(partes):
            errores['arete_3'] = 'El arete lleva sus 3 partes; sin arete, deja las 3 vacías.'
        if self.categoria_id and self.especie_id and self.categoria.especie_id != self.especie_id:
            errores['categoria_id'] = f'La categoría {self.categoria} no es de {self.especie}.'
        elif self.categoria_id and self.categoria.sexo and self.sexo and self.categoria.sexo != self.sexo:
            errores['sexo'] = f'La categoría {self.categoria} es de {self.categoria.get_sexo_display().lower()}s.'
        if self.fecha_nacimiento and self.fecha_nacimiento > timezone.localdate():
            errores['fecha_nacimiento'] = 'La fecha de nacimiento no puede ser futura.'
        if errores:
            raise ValidationError(errores)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.folio:
            self.folio = f'GAN-{self.pk:05d}'
            Animal.objects.filter(pk=self.pk).update(folio=self.folio)

    def __str__(self):
        return self.identificacion


class EdicionAnimal(models.Model):
    """Bitácora de cambios a los datos del animal (arete, categoría…): [{campo, antes, despues}]."""
    animal = models.ForeignKey(Animal, on_delete=models.CASCADE, related_name='ediciones')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='ediciones_ganado')
    fecha = models.DateTimeField(default=timezone.now)
    cambios = models.JSONField(default=list)

    class Meta:
        ordering = ['fecha', 'id']


ALTA = 'ALTA'
BAJA = 'BAJA'
TRASPASO = 'TRASPASO'
TIPOS = [(ALTA, 'Alta'), (BAJA, 'Baja'), (TRASPASO, 'Traspaso')]

COMPRA = 'COMPRA'
NACIMIENTO = 'NACIMIENTO'
REGALO = 'REGALO'
VENTA = 'VENTA'
MUERTE = 'MUERTE'
MOTIVOS = [
    (COMPRA, 'Compra'), (NACIMIENTO, 'Nacimiento'), (REGALO, 'Regalo'),
    (VENTA, 'Venta'), (MUERTE, 'Muerte'),
]
MOTIVOS_POR_TIPO = {ALTA: {COMPRA, NACIMIENTO, REGALO}, BAJA: {VENTA, MUERTE}, TRASPASO: {''}}


class Movimiento(models.Model):
    """
    Alta: llegan a `rancho`. Baja: salen de `rancho`. Traspaso: de `rancho` a
    `rancho_destino` (solo entre ranchos propios; lo que viene de fuera es
    compra o regalo).
    """
    tipo = models.CharField(max_length=10, choices=TIPOS)
    motivo = models.CharField(max_length=12, choices=MOTIVOS, blank=True)
    fecha = models.DateField()
    rancho = models.ForeignKey(Rancho, on_delete=models.PROTECT, related_name='movimientos')
    rancho_destino = models.ForeignKey(
        Rancho, on_delete=models.PROTECT, related_name='movimientos_destino', null=True, blank=True,
    )
    contraparte = models.CharField(max_length=150, blank=True, help_text='Proveedor, comprador o quien regaló.')
    importe = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)],
    )
    notas = models.TextField(blank=True, help_text='En muertes, la causa.')
    animales = models.ManyToManyField(Animal, through='MovimientoAnimal', related_name='movimientos')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='movimientos_ganado')
    creado = models.DateTimeField(default=timezone.now)
    editado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True, blank=True,
    )
    editado_en = models.DateTimeField(null=True, blank=True)
    cancelado = models.BooleanField(default=False)
    motivo_cancelacion = models.CharField(max_length=255, blank=True)
    usuario_cancelacion = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True, blank=True,
    )
    cancelado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-fecha', '-id']

    @property
    def folio(self):
        return f'MG-{self.pk:05d}'

    def __str__(self):
        return self.folio


class MovimientoAnimal(models.Model):
    movimiento = models.ForeignKey(Movimiento, on_delete=models.CASCADE, related_name='lineas')
    animal = models.ForeignKey(Animal, on_delete=models.PROTECT, related_name='lineas_movimiento')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['movimiento', 'animal'], name='ganado_movimiento_animal_unico'),
        ]
