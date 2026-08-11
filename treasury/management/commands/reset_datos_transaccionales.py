from django.core.management.base import BaseCommand
from django.db import transaction

from treasury.models import ArqueoCaja, CorteCaja, MovimientoTesoreria, NominaSemanal


class Command(BaseCommand):
    help = (
        "Borra los datos transaccionales de Tesorería: movimientos, cortes de caja, "
        "arqueos y nóminas semanales (con sus detalles). No toca catálogos (divisas, "
        "denominaciones, bancos, roles, empleados, ranchos, puestos) ni usuarios."
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

        n_arqueos = ArqueoCaja.objects.count()
        n_movimientos = MovimientoTesoreria.objects.count()
        n_cortes = CorteCaja.objects.count()
        n_nominas = NominaSemanal.objects.count()

        with transaction.atomic():
            # Los arqueos y movimientos protegen (PROTECT) al corte de caja al que
            # pertenecen, así que deben borrarse antes que el corte.
            ArqueoCaja.objects.all().delete()
            MovimientoTesoreria.objects.all().delete()
            CorteCaja.objects.all().delete()
            NominaSemanal.objects.all().delete()

        self.stdout.write(self.style.SUCCESS(
            f"Borrados: {n_arqueos} arqueos, {n_movimientos} movimientos, "
            f"{n_cortes} cortes de caja y {n_nominas} nóminas semanales (con sus detalles)."
        ))

    def confirmar(self):
        respuesta = input(
            "Esto borrará TODOS los movimientos, cortes de caja, arqueos y nóminas "
            "semanales. Los catálogos y usuarios no se tocan. ¿Continuar? [s/N]: "
        )
        return respuesta.strip().lower() in ("s", "si", "sí", "y", "yes")
