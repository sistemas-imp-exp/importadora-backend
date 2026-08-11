import random
from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from treasury.models import CorteCaja, Divisa, MovimientoDivisa, MovimientoTesoreria, SaldoCaja

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
        "Genera cortes de caja y movimientos de prueba (ingresos, egresos, ediciones, cancelaciones) "
        "en días ANTERIORES al corte más antiguo que ya exista (o a hoy, si la base está vacía). "
        "Al corte que quede abierto (el actual) también le agrega movimientos."
    )

    def add_arguments(self, parser):
        parser.add_argument("--cortes", type=int, default=3, help="Cuántos cortes históricos (cerrados) crear antes de hoy.")
        parser.add_argument("--min-movimientos", type=int, default=20)
        parser.add_argument("--max-movimientos", type=int, default=30)

    def handle(self, *args, **options):
        usuarios = list(User.objects.all())
        if not usuarios:
            self.stderr.write("No hay usuarios en la base; crea al menos uno antes de sembrar datos.")
            return

        divisas = list(Divisa.objects.filter(activa=True))
        if not divisas:
            self.stderr.write("No hay divisas activas; crea al menos una antes de sembrar datos.")
            return

        n_cortes = options["cortes"]
        min_m, max_m = options["min_movimientos"], options["max_movimientos"]

        # Los cortes nuevos siempre van ANTES del más antiguo que ya exista
        # (o antes de hoy, si la base está vacía) -- nunca en el futuro.
        corte_mas_antiguo = CorteCaja.objects.order_by("fecha").first()
        fecha_limite = corte_mas_antiguo.fecha.date() if corte_mas_antiguo else timezone.localdate()
        fecha_base = fecha_limite - timedelta(days=n_cortes)

        run_id = timezone.now().strftime("%m%d%H%M%S")
        folio_seq = 1
        resumen = []
        saldos_previos = {}  # divisa_id -> Decimal, se encadena corte a corte

        for i in range(n_cortes):
            fecha_dia = fecha_base + timedelta(days=i)
            fecha_apertura = timezone.make_aware(datetime(fecha_dia.year, fecha_dia.month, fecha_dia.day, 9, 0))
            responsable_apertura = random.choice(usuarios)

            # Se construye directo (sin CorteCaja.abrir_nuevo_corte()) porque
            # ese método exige que no haya ningún corte abierto y hereda del
            # más reciente -- lo contrario de lo que necesitamos al insertar
            # historia hacia atrás mientras el corte de hoy sigue abierto.
            corte = CorteCaja.objects.create(
                fecha=fecha_apertura,
                responsable_apertura=responsable_apertura,
                observaciones=f"Corte de prueba generado automáticamente #{i + 1}",
            )
            for divisa in divisas:
                inicial = saldos_previos.get(divisa.id, Decimal("0"))
                SaldoCaja.objects.create(corte=corte, divisa=divisa, saldo_inicial=inicial, saldo_final=inicial)

            n_movs, ingresos, egresos, editados, cancelados, folio_seq = self._generar_movimientos(
                corte, divisas, usuarios, responsable_apertura, min_m, max_m, run_id, folio_seq
            )

            fecha_cierre = fecha_apertura + timedelta(hours=random.randint(8, 12))
            corte.close(responsable_cierre=random.choice(usuarios), fecha_cierre=fecha_cierre)

            saldos_previos = {s.divisa_id: s.saldo_final for s in corte.saldos.all()}

            resumen.append(
                f"Corte #{corte.id} ({fecha_dia}) - CERRADO | {n_movs} movimientos: "
                f"{ingresos} ingresos, {egresos} egresos, {editados} editados, {cancelados} cancelados"
            )

        # El corte "vivo" también recibe movimientos, para tener algo con qué
        # probar de inmediato en la app sin tener que abrir uno a mano.
        corte_abierto = CorteCaja.abierto()
        if corte_abierto is None:
            corte_abierto = CorteCaja.abrir_nuevo_corte(
                fecha=timezone.make_aware(datetime(fecha_limite.year, fecha_limite.month, fecha_limite.day, 9, 0)),
                responsable_apertura=random.choice(usuarios),
                observaciones="Corte de prueba generado automáticamente (abierto)",
            )
        elif saldos_previos and corte_abierto.fecha.date() == fecha_limite:
            # Solo se ajusta el saldo inicial si el corte abierto es
            # justo el que sigue cronológicamente a la historia recién
            # creada -- si no, no sabemos qué hay entre medio y se deja tal cual.
            for divisa_id, saldo_valor in saldos_previos.items():
                saldo = SaldoCaja.objects.filter(corte=corte_abierto, divisa_id=divisa_id).first()
                if saldo:
                    saldo.saldo_inicial = saldo_valor
                    saldo.save()
                    saldo.actualizar_balance()  # recalcula saldo_final con la actividad real que ya tuviera

        n_movs, ingresos, egresos, editados, cancelados, folio_seq = self._generar_movimientos(
            corte_abierto, divisas, usuarios, corte_abierto.responsable_apertura, min_m, max_m, run_id, folio_seq
        )
        resumen.append(
            f"Corte #{corte_abierto.id} ({corte_abierto.fecha.date()}) - ABIERTO (el actual) | "
            f"{n_movs} movimientos: {ingresos} ingresos, {egresos} egresos, {editados} editados, {cancelados} cancelados"
        )

        self.stdout.write(self.style.SUCCESS("Datos de prueba generados:"))
        for linea in resumen:
            self.stdout.write(f"  - {linea}")

    def _generar_movimientos(self, corte, divisas, usuarios, responsable_defecto, min_m, max_m, run_id, folio_seq):
        n_movs = random.randint(min_m, max_m)
        ingresos = egresos = editados = cancelados = 0

        for _ in range(n_movs):
            divisa = random.choice(divisas)
            disponible = SaldoCaja.objects.get(corte=corte, divisa=divisa).saldo_disponible
            puede_egreso = disponible > Decimal("50")

            tipo = random.choices(
                [MovimientoTesoreria.INGRESO, MovimientoTesoreria.EGRESO],
                weights=[65, 35] if puede_egreso else [100, 0],
            )[0]

            if tipo == MovimientoTesoreria.INGRESO:
                cantidad = Decimal(random.randint(100, 8000))
                ingresos += 1
            else:
                tope = max(int(min(disponible, Decimal("6000"))), 50)
                cantidad = Decimal(random.randint(50, tope))
                egresos += 1

            folio = f"SEED-{run_id}-{folio_seq:04d}"
            folio_seq += 1

            with transaction.atomic():
                movimiento = MovimientoTesoreria.objects.create(
                    corte=corte,
                    fecha=corte.fecha,
                    folio=folio,
                    tipo=tipo,
                    autorizo=random.choice(AUTORIZO_NOMBRES),
                    beneficiario=random.choice(BENEFICIARIOS),
                    concepto=random.choice(CONCEPTOS),
                    usuario=responsable_defecto,
                )
                MovimientoDivisa.objects.create(movimiento=movimiento, divisa=divisa, cantidad=cantidad)

            dado = random.random()
            if dado < 0.15:
                linea = movimiento.divisas.first()
                ajuste = Decimal(random.randint(-200, 200))
                linea.cantidad = max(linea.cantidad + ajuste, Decimal("10"))
                linea.save()
                movimiento.beneficiario = random.choice(BENEFICIARIOS)
                movimiento.editado = True
                movimiento.save()
                editados += 1
            elif dado < 0.25 and not corte.cerrado:
                movimiento.cancelar(usuario=random.choice(usuarios), motivo=random.choice(MOTIVOS_CANCELACION))
                cancelados += 1

        return n_movs, ingresos, egresos, editados, cancelados, folio_seq
