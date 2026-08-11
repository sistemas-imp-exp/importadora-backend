from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from security.models import Area
from treasury.models import Banco, Denominacion, Divisa

AREAS = [
    {"codigo": "TES", "nombre": "Tesorería"},
]

BANCOS = [
    "Oxxo",
    "Coppel",
    "BBVA",
]

# Denominaciones vigentes según el banco central de cada país (billetes de alta
# denominación ya descontinuados, como el de 500 EUR, se dejan fuera a propósito).
DIVISAS = [
    {
        "codigo": "GTQ",
        "nombre": "Quetzal",
        "simbolo": "Q",
        "billetes": [1000, 500, 200, 100, 50, 20, 10, 5, 1],
        "monedas": [5, 2, 1, Decimal("0.50"), Decimal("0.25"), Decimal("0.10"), Decimal("0.05"), Decimal("0.01")],
    },
    {
        "codigo": "MXN",
        "nombre": "Peso mexicano",
        "simbolo": "$",
        "billetes": [1000, 500, 200, 100, 50, 20],
        "monedas": [20, 10, 5, 2, 1, Decimal("0.50")],
    },
    {
        "codigo": "DOP",
        "nombre": "Peso dominicano",
        "simbolo": "RD$",
        "billetes": [2000, 1000, 500, 200, 100, 50, 20],
        "monedas": [25, 10, 5, 1],
    },
    {
        "codigo": "USD",
        "nombre": "Dólar estadounidense",
        "simbolo": "$",
        "billetes": [100, 50, 20, 10, 5, 2, 1],
        "monedas": [Decimal("0.25"), Decimal("0.10"), Decimal("0.05"), Decimal("0.01")],
    },
    {
        "codigo": "EUR",
        "nombre": "Euro",
        "simbolo": "€",
        "billetes": [200, 100, 50, 20, 10, 5],
        "monedas": [2, 1, Decimal("0.50"), Decimal("0.20"), Decimal("0.10"), Decimal("0.05"), Decimal("0.02"), Decimal("0.01")],
    },
]


class Command(BaseCommand):
    help = "Carga los datos iniciales de Tesorería: rol TES, bancos y divisas con sus denominaciones."

    @transaction.atomic
    def handle(self, *args, **options):
        self.cargar_areas()
        self.cargar_bancos()
        self.cargar_divisas()

    def cargar_areas(self):
        for area in AREAS:
            _, creada = Area.objects.get_or_create(
                codigo=area["codigo"],
                defaults={"nombre": area["nombre"], "activo": True},
            )
            self.avisar("Rol", area["codigo"], creada)

    def cargar_bancos(self):
        for nombre in BANCOS:
            _, creado = Banco.objects.get_or_create(
                nombre=nombre,
                defaults={"activo": True},
            )
            self.avisar("Banco", nombre, creado)

    def cargar_divisas(self):
        for datos in DIVISAS:
            divisa, creada = Divisa.objects.get_or_create(
                codigo=datos["codigo"],
                defaults={
                    "nombre": datos["nombre"],
                    "simbolo": datos["simbolo"],
                    "activa": True,
                },
            )
            self.avisar("Divisa", datos["codigo"], creada)

            for valor in datos["billetes"]:
                self.cargar_denominacion(divisa, valor, Denominacion.BILLETE)

            for valor in datos["monedas"]:
                self.cargar_denominacion(divisa, valor, Denominacion.MONEDA)

    def cargar_denominacion(self, divisa, valor, tipo):
        _, creada = Denominacion.objects.get_or_create(
            divisa=divisa,
            valor=Decimal(valor),
            tipo=tipo,
            defaults={"activa": True},
        )
        etiqueta = "billete" if tipo == Denominacion.BILLETE else "moneda"
        self.avisar(f"  {divisa.codigo} {etiqueta}", str(valor), creada)

    def avisar(self, tipo, nombre, creado):
        if creado:
            self.stdout.write(self.style.SUCCESS(f"{tipo} '{nombre}' creado."))
        else:
            self.stdout.write(f"{tipo} '{nombre}' ya existía, se omitió.")
