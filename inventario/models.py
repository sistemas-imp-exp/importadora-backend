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
