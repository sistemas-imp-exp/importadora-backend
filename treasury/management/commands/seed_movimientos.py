import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from treasury.models import AperturaDivisa, AperturaPeriodo, Divisa, MovimientoDivisa, MovimientoTesoreria
from treasury.saldos import saldos_al

User = get_user_model()

AUTORIZO_NOMBRES = ["Gerencia", "Contraloría", "Dirección General", "Beatriz Hernández", "Admin"]
BENEFICIARIOS = [
    "Proveedor Mariscos del Golfo", "Transportes La Costa", "Papelería El Puerto",
    "Servicios de Limpieza Marina", "Gasolinera Puerto Azul", "Cliente Mostrador",
    "Reparaciones Refrigeración", "Aduana / Trámites", "Comisión bancaria", "Empaques y Hielo SA",
]
CONCEPTOS = [
    "Pago a proveedor", "Venta de contado", "Compra de insumos", "Gasolina camioneta reparto",
    "Anticipo de cliente", "Reembolso de caja chica", "Pago de fletes", "Comisión bancaria",
    "Cobro de factura", "Compra de hielo y empaques",
]
MOTIVOS_CANCELACION = [
    "Folio duplicado por error de captura",
    "Monto capturado incorrectamente",
    "Movimiento registrado por error",
    "Beneficiario incorrecto, se vuelve a capturar",
]


class Command(BaseCommand):
    help = (
        "Genera datos de prueba de caja: una apertura (saldos iniciales) al inicio del "
        "periodo si no existe ninguna, y movimientos diarios (ingresos, egresos, ediciones, "
        "cancelaciones) en los últimos N días hasta hoy."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dias", type=int, default=10, help="Cuántos días hacia atrás (incluido hoy) generar.")
        parser.add_argument("--min-movimientos", type=int, default=5, help="Mínimo de movimientos por día.")
        parser.add_argument("--max-movimientos", type=int, default=12, help="Máximo de movimientos por día.")

    def handle(self, *args, **options):
        usuarios = list(User.objects.all())
        if not usuarios:
            self.stderr.write("No hay usuarios en la base; crea al menos uno antes de sembrar datos.")
            return
        divisas = list(Divisa.objects.filter(activa=True))
        if not divisas:
            self.stderr.write("No hay divisas activas; crea al menos una antes de sembrar datos.")
            return

        hoy = timezone.localdate()
        inicio = hoy - timedelta(days=options["dias"] - 1)
        primera = AperturaPeriodo.primera()
        if primera is None or primera.fecha > inicio:
            apertura = AperturaPeriodo.objects.create(
                fecha=inicio, creado_por=random.choice(usuarios), observaciones="Apertura de prueba",
            )
            for divisa in divisas:
                AperturaDivisa.objects.create(apertura=apertura, divisa=divisa, monto=Decimal(random.randint(5000, 20000)))
            self.stdout.write(f"  - Apertura de prueba el {inicio:%d/%m/%Y}")

        run_id = timezone.now().strftime("%m%d%H%M%S")
        folio_seq = 1
        for i in range(options["dias"]):
            dia = inicio + timedelta(days=i)
            n = random.randint(options["min_movimientos"], options["max_movimientos"])
            for _ in range(n):
                folio_seq = self._movimiento(dia, divisas, usuarios, run_id, folio_seq)
            self.stdout.write(f"  - {dia:%d/%m/%Y}: {n} movimientos")
        self.stdout.write(self.style.SUCCESS("Datos de prueba generados."))

    def _movimiento(self, dia, divisas, usuarios, run_id, folio_seq):
        divisa = random.choice(divisas)
        disponible = saldos_al(dia).get(divisa.id, Decimal("0"))
        es_egreso = disponible > Decimal("50") and random.random() < 0.35
        tipo = MovimientoTesoreria.EGRESO if es_egreso else MovimientoTesoreria.INGRESO
        cantidad = (
            Decimal(random.randint(50, max(int(min(disponible, Decimal("6000"))), 50)))
            if es_egreso else Decimal(random.randint(100, 8000))
        )
        with transaction.atomic():
            movimiento = MovimientoTesoreria.objects.create(
                fecha=dia,
                folio=f"SEED-{run_id}-{folio_seq:04d}",
                tipo=tipo,
                autorizo=random.choice(AUTORIZO_NOMBRES),
                beneficiario=random.choice(BENEFICIARIOS),
                concepto=random.choice(CONCEPTOS),
                usuario=random.choice(usuarios),
            )
            MovimientoDivisa.objects.create(movimiento=movimiento, divisa=divisa, cantidad=cantidad)

        dado = random.random()
        if dado < 0.1:
            movimiento.beneficiario = random.choice(BENEFICIARIOS)
            movimiento.editado = True
            movimiento.editado_por = random.choice(usuarios)
            movimiento.editado_en = timezone.now()
            movimiento.save()
        elif dado < 0.18:
            movimiento.cancelar(usuario=random.choice(usuarios), motivo=random.choice(MOTIVOS_CANCELACION))
        return folio_seq + 1
