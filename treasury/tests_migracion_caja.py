"""La migración 0014 (caja por fecha) corre sobre una base que ya tiene cortes, movimientos y arqueos."""
from datetime import datetime

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

ANTES = [("treasury", "0013_movimientotesoreria_movtesoreria_fecha_idx")]
DESPUES = [("treasury", "0014_caja_por_fecha")]


class MigracionCajaPorFechaTests(TransactionTestCase):
    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(ANTES)
        apps = executor.loader.project_state(ANTES).apps

        User = apps.get_model("auth", "User")
        Divisa = apps.get_model("treasury", "Divisa")
        CorteCaja = apps.get_model("treasury", "CorteCaja")
        MovimientoTesoreria = apps.get_model("treasury", "MovimientoTesoreria")
        ArqueoCaja = apps.get_model("treasury", "ArqueoCaja")

        usuario = User.objects.create(username="cajero")
        Divisa.objects.create(codigo="MXN", nombre="Peso", simbolo="$")
        self.fecha_corte = timezone.make_aware(datetime(2026, 9, 2, 9, 0))
        corte = CorteCaja.objects.create(fecha=self.fecha_corte, responsable_apertura=usuario)
        for folio, fecha in (("1", self.fecha_corte), ("2", timezone.make_aware(datetime(2026, 9, 2, 23, 30)))):
            MovimientoTesoreria.objects.create(
                corte=corte, fecha=fecha, folio=folio, tipo="I", autorizo="a", beneficiario="b",
                concepto="c", usuario=usuario,
            )
        ArqueoCaja.objects.create(
            corte=corte, hora_inicio=self.fecha_corte, hora_termino=self.fecha_corte, usuario=usuario,
        )

    def tearDown(self):
        # Deja la base de pruebas en la última migración para el resto de la suite.
        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())

    def test_migra_con_datos_existentes(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(DESPUES)
        apps = executor.loader.project_state(DESPUES).apps

        fechas = set(apps.get_model("treasury", "MovimientoTesoreria").objects.values_list("fecha", flat=True))
        arqueo = apps.get_model("treasury", "ArqueoCaja").objects.get()
        # Ambos son del 2 de septiembre en hora de México, aunque el de las 23:30 ya sea día 3 en UTC.
        self.assertEqual(fechas, {self.fecha_corte.date()})
        self.assertEqual(arqueo.fecha, self.fecha_corte.date())
        # Sin cortes ni saldos guardados: la caja parte de una apertura nueva.
        self.assertEqual(apps.get_model("treasury", "AperturaPeriodo").objects.count(), 0)
