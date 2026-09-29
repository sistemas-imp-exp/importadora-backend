from django.core.management.base import BaseCommand
from django.db import transaction

from treasury.models import AperturaPeriodo, ArqueoCaja, ConfiguracionFolio, MovimientoTesoreria, NominaSemanal


class Command(BaseCommand):
    help = (
        "Borra los datos transaccionales de Tesorería: movimientos (con sus adjuntos), "
        "saldos iniciales (aperturas), arqueos, nóminas semanales (con sus detalles) y "
        "reinicia los folios. No toca catálogos (divisas, denominaciones, bancos, roles, "
        "empleados, ranchos, puestos) ni usuarios."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Omite la confirmación interactiva.",
        )

    def handle(self, *args, **options):
        if not options["yes"] and not self.confirmar():
            self.stdout.write("Cancelado.")
            return

        conteos = {
            "arqueos": ArqueoCaja.objects.count(),
            "movimientos": MovimientoTesoreria.objects.count(),
            "aperturas": AperturaPeriodo.objects.count(),
            "nóminas semanales": NominaSemanal.objects.count(),
        }

        with transaction.atomic():
            ArqueoCaja.objects.all().delete()
            MovimientoTesoreria.objects.all().delete()
            AperturaPeriodo.objects.all().delete()
            NominaSemanal.objects.all().delete()
            ConfiguracionFolio.objects.all().delete()

        resumen = ", ".join(f"{n} {nombre}" for nombre, n in conteos.items())
        self.stdout.write(self.style.SUCCESS(f"Borrados: {resumen}. Folios reiniciados."))

    def confirmar(self):
        respuesta = input(
            "Esto borrará TODOS los movimientos, saldos iniciales, arqueos y nóminas "
            "semanales, y reiniciará los folios. Los catálogos y usuarios no se tocan. "
            "¿Continuar? [s/N]: "
        )
        return respuesta.strip().lower() in ("s", "si", "sí", "y", "yes")
