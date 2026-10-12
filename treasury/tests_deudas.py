from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from security.permissions import AREA_TESORERIA
from security.testing import crear_usuario_con_area

from .models import Deuda, Deudor, Divisa, MovimientoTesoreria
from .saldos import saldos_al

HOY = timezone.localdate()


class DeudasApiTests(APITestCase):
    def setUp(self):
        self.usuario = crear_usuario_con_area("cajero", AREA_TESORERIA)
        self.client.force_authenticate(user=self.usuario)
        self.mxn = Divisa.objects.create(codigo="MXN", nombre="Peso", simbolo="$")
        self.usd = Divisa.objects.create(codigo="USD", nombre="Dólar", simbolo="US$")
        self.deudor = Deudor.objects.create(nombre="Juan Pérez", tipo=Deudor.TRABAJADOR, usuario=self.usuario)
        self.deuda = Deuda.objects.create(
            deudor=self.deudor, concepto="Venta de camarón", divisa=self.mxn, monto=Decimal("1000"),
            fecha=HOY - timedelta(days=5), usuario=self.usuario,
        )
        self.folio = 0

    def _abonar(self, cantidad, deuda=None, divisas=None, tipo="I", fecha=None):
        self.folio += 1
        return self.client.post("/api/treasury/movimientos/", {
            "fecha": (fecha or HOY).isoformat(), "folio": f"A-{self.folio}", "tipo": tipo,
            "autorizo": "Jefe", "beneficiario": "Juan Pérez", "concepto": "Abono",
            "deuda_id": (deuda or self.deuda).id,
            "divisas": divisas or [{"divisa_id": self.mxn.id, "cantidad": cantidad}],
        }, format="json")

    def _deuda(self):
        return self.client.get(f"/api/treasury/deudas/{self.deuda.id}/").data

    def test_crear_deudor_y_deuda_registra_al_usuario_de_la_sesion(self):
        r = self.client.post("/api/treasury/deudores/", {"nombre": "  Pesca   Sur ", "tipo": "E"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["nombre"], "Pesca Sur")
        r = self.client.post("/api/treasury/deudas/", {
            "deudor_id": r.data["id"], "concepto": "Venta de pulpo", "divisa_id": self.usd.id,
            "monto": "500", "fecha": HOY.isoformat(),
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["usuario"]["username"], "cajero")
        self.assertEqual((r.data["estado"], r.data["saldo"]), ("abierta", "500.00"))

    def test_la_deuda_no_mueve_la_caja(self):
        self.assertEqual(saldos_al(HOY), {})

    def test_abono_es_un_ingreso_que_baja_el_saldo_y_queda_en_el_historial(self):
        r = self._abonar("300")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["deuda"]["deudor"]["nombre"], "Juan Pérez")
        deuda = self._deuda()
        self.assertEqual((deuda["abonado"], deuda["saldo"], deuda["estado"]), ("300.00", "700.00", "abierta"))
        self.assertEqual([a["folio"] for a in deuda["abonos"]], ["A-1"])
        self.assertEqual(saldos_al(HOY)[self.mxn.id], Decimal("300"))

    def test_abonar_el_saldo_completo_la_marca_pagada(self):
        self.assertEqual(self._abonar("600").status_code, 201)
        self.assertEqual(self._abonar("400").status_code, 201)
        self.assertEqual(self._deuda()["estado"], "pagada")
        r = self.client.get("/api/treasury/deudas/", {"estado": "abierta"})
        self.assertEqual(r.data, [])

    def test_no_se_abona_mas_que_el_saldo_pendiente(self):
        self._abonar("900")
        r = self._abonar("100.01")
        self.assertEqual(r.status_code, 400)
        self.assertIn("saldo pendiente", str(r.data["divisas"]))

    def test_validaciones_del_abono(self):
        self.assertIn("ingreso", str(self._abonar("10", tipo="E").data))
        r = self._abonar("10", divisas=[{"divisa_id": self.usd.id, "cantidad": "10"}])
        self.assertIn("MXN", str(r.data["divisas"]))
        r = self._abonar("10", divisas=[
            {"divisa_id": self.mxn.id, "cantidad": "10"}, {"divisa_id": self.usd.id, "cantidad": "10"},
        ])
        self.assertEqual(r.status_code, 400)
        r = self._abonar("10", fecha=HOY - timedelta(days=6))
        self.assertIn("anterior", str(r.data["fecha"]))
        self.assertFalse(MovimientoTesoreria.objects.exists())

    def test_cancelar_el_ingreso_revierte_el_abono_y_queda_en_historial(self):
        movimiento_id = self._abonar("1000").data["id"]
        self.client.post(f"/api/treasury/movimientos/{movimiento_id}/cancelar/", {"motivo": "Error"}, format="json")
        deuda = self._deuda()
        self.assertEqual((deuda["saldo"], deuda["estado"]), ("1000.00", "abierta"))
        self.assertTrue(deuda["abonos"][0]["cancelado"])

    def test_editar_el_ingreso_valida_el_saldo_sin_contarse_a_si_mismo(self):
        r = self._abonar("1000")
        datos = {
            "fecha": HOY.isoformat(), "folio": "A-1", "tipo": "I", "autorizo": "Jefe", "beneficiario": "Juan",
            "concepto": "Abono", "divisas": [{"divisa_id": self.mxn.id, "cantidad": "800"}],
        }
        self.assertEqual(self.client.put(f"/api/treasury/movimientos/{r.data['id']}/", datos, format="json").status_code, 200)
        datos["divisas"][0]["cantidad"] = "1000.01"
        self.assertEqual(self.client.put(f"/api/treasury/movimientos/{r.data['id']}/", datos, format="json").status_code, 400)

    def test_deuda_con_abonos_no_se_edita_ni_se_cancela(self):
        datos = {"deudor_id": self.deudor.id, "concepto": "Otro", "divisa_id": self.mxn.id,
                 "monto": "2000", "fecha": HOY.isoformat()}
        r = self.client.put(f"/api/treasury/deudas/{self.deuda.id}/", datos, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["editado_por"]["username"], "cajero")
        self._abonar("10")
        self.assertEqual(self.client.put(f"/api/treasury/deudas/{self.deuda.id}/", datos, format="json").status_code, 400)
        r = self.client.post(f"/api/treasury/deudas/{self.deuda.id}/cancelar/", {"motivo": "x"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_cancelar_deuda_pide_motivo_y_bloquea_abonos(self):
        url = f"/api/treasury/deudas/{self.deuda.id}/cancelar/"
        self.assertEqual(self.client.post(url, {"motivo": " "}, format="json").status_code, 400)
        r = self.client.post(url, {"motivo": "Se condonó"}, format="json")
        self.assertEqual((r.status_code, r.data["estado"]), (200, "cancelada"))
        self.assertIn("cancelada", str(self._abonar("10").data))

    def test_deudores_con_deuda_abierta_para_el_buscador(self):
        otro = Deudor.objects.create(nombre="Sin deuda", tipo=Deudor.EXTERNO, usuario=self.usuario)
        r = self.client.get("/api/treasury/deudores/", {"con_deuda_abierta": "1"})
        self.assertEqual([d["id"] for d in r.data], [self.deudor.id])
        self.assertEqual(r.data[0]["deudas_abiertas"], 1)
        r = self.client.get("/api/treasury/deudores/", {"q": "sin"})
        self.assertEqual([d["id"] for d in r.data], [otro.id])

    def test_estado_de_cuenta_pdf_y_excel(self):
        self._abonar("250")
        r = self.client.get(f"/api/treasury/deudores/{self.deudor.id}/estado-cuenta/pdf/")
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/pdf"))
        r = self.client.get(f"/api/treasury/deudores/{self.deudor.id}/estado-cuenta/excel/")
        self.assertEqual(r.status_code, 200)

    def test_solo_lectura_consulta_pero_no_abona(self):
        lector = crear_usuario_con_area("lector", AREA_TESORERIA, solo_lectura=True)
        self.client.force_authenticate(user=lector)
        self.assertEqual(self.client.get("/api/treasury/deudas/").status_code, 200)
        self.assertEqual(self.client.get(f"/api/treasury/deudores/{self.deudor.id}/estado-cuenta/pdf/").status_code, 200)
        self.assertEqual(self._abonar("10").status_code, 403)
        self.assertEqual(self.client.post("/api/treasury/deudores/", {"nombre": "X", "tipo": "E"}).status_code, 403)
