from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from openpyxl import load_workbook


from .antiguedad import RANGOS, rango
from .models import AjusteCliente, AplicacionPago, PagoCliente
from .saldos import saldos_por_cliente
from .tests import CORTE, HOY, BaseCartera, _preparar_ventas

URL = "/api/cartera/reportes/antiguedad/"


class AntiguedadSaldosTests(BaseCartera):
    """
    Pagos aplicados a notas y antigüedad de saldos ("Cobro a clientes 2026").
    Datos (ver _preparar_ventas): IMPORTADORA saldo al corte 1,000 + SAL-1
    14,255 (hoy); SELECTOS −200 al corte + SAL-2 2,000 (hoy).
    """

    def setUp(self):
        super().setUp()
        _preparar_ventas(self)
        self.sal1 = self.l1.salida
        self.sal2 = self.l3.salida

    def _reporte(self, **params):
        return self.client.get(URL, params).data

    def _bloques(self, datos=None):
        datos = datos or self._reporte()
        fila = next(f for f in datos["clientes"] if f["cliente"]["id"] == self.cliente.id)
        return fila, {b["empresa"]["nombre"]: b for b in fila["empresas"]}

    def _notas(self, empresa):
        datos = self.client.get(f"/api/cartera/clientes/{self.cliente.id}/notas-pendientes/", {"empresa": empresa.id}).data
        return {n["nota"]: n for n in datos}

    def test_rangos_de_dias_del_area(self):
        self.assertEqual([e for e, _ in RANGOS], ["1-15", "16-30", "31-45", "46-89", "90 O MÁS"])
        casos = {0: 0, 1: 0, 15: 0, 16: 1, 30: 1, 31: 2, 45: 2, 46: 3, 89: 3, 90: 4, 400: 4}
        self.assertEqual({d: rango(d) for d in casos}, casos)

    def test_sin_aplicar_el_pago_baja_lo_mas_antiguo_y_cuadra_con_el_saldo(self):
        self.assertEqual(self._pago("1300").status_code, 201)
        fila, bloques = self._bloques()
        imp = bloques["IMPORTADORA"]
        # El saldo al corte (lo más antiguo) queda pagado; a SAL-1 le bajan 300.
        self.assertEqual([(n["nota"], n["descontado"], n["pendiente"]) for n in imp["notas"]],
                         [("N-SAL-1", "300.00", "13955.00")])
        # En SELECTOS el saldo a favor al corte se descuenta de la venta.
        sel = bloques["MARISCOS SELECTOS"]
        self.assertEqual([(n["nota"], n["pendiente"]) for n in sel["notas"]], [("N-SAL-2", "1800.00")])
        saldos = saldos_por_cliente([self.cliente.id])[self.cliente.id]
        self.assertEqual((Decimal(imp["saldo"]), Decimal(sel["saldo"])), (saldos[self.imp.id], saldos[self.sel.id]))
        self.assertEqual(fila["conjunto"], "15755.00")
        self.assertEqual(imp["por_rango"], ["13955.00", "0.00", "0.00", "0.00", "0.00"])

    def test_pago_aplicado_a_una_nota_y_el_resto_a_lo_mas_antiguo(self):
        r = self._pago("5000", aplicaciones=[{"salida_id": self.sal1.id, "monto": "4000"}])
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["aplicaciones"], [{"salida_id": self.sal1.id, "monto": "4000.00", "nota": "N-SAL-1"}])
        _, bloques = self._bloques()
        # 4,000 a SAL-1; los 1,000 restantes liquidan el saldo al corte.
        self.assertEqual([(n["nota"], n["pendiente"]) for n in bloques["IMPORTADORA"]["notas"]], [("N-SAL-1", "10255.00")])
        notas = self._notas(self.imp)
        self.assertEqual((notas["N-SAL-1"]["pendiente"], notas["N-SAL-1"]["disponible"]), ("10255.00", "10255.00"))

    def test_abono_al_saldo_al_corte_y_pago_parcial(self):
        r = self._pago("400", aplicaciones=[{"salida_id": None, "monto": "400"}])
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["aplicaciones"][0]["nota"], "Saldo al corte")
        _, bloques = self._bloques()
        corte = bloques["IMPORTADORA"]["notas"][0]
        self.assertEqual((corte["tipo"], corte["nota"], corte["pendiente"]), ("inicial", f"SALDO AL {CORTE:%d/%m/%Y}", "600.00"))

    def test_validaciones_de_lo_aplicado(self):
        ajena = self.SalidaDetalle.objects.filter(salida__cliente__nombre="OTRO").first().salida
        casos = [
            # Pasa del monto del pago.
            ({"monto": "100", "aplicaciones": [{"salida_id": self.sal1.id, "monto": "150"}]}, "pasa del monto"),
            # Nota repetida.
            ({"aplicaciones": [{"salida_id": self.sal1.id, "monto": "10"}, {"salida_id": self.sal1.id, "monto": "10"}]},
             "dos veces"),
            # Nota de otro cliente o de otra empresa.
            ({"aplicaciones": [{"salida_id": ajena.id, "monto": "1"}]}, "no es una venta de este cliente"),
            ({"aplicaciones": [{"salida_id": self.sal2.id, "monto": "1"}]}, "no es una venta de este cliente"),
            # La nota es posterior al pago.
            ({"fecha": CORTE + timedelta(days=1), "aplicaciones": [{"salida_id": self.sal1.id, "monto": "1"}]},
             "posterior a la fecha del pago"),
            # Pago por identificar.
            ({"cliente_id": None, "aplicaciones": [{"salida_id": None, "monto": "1"}]}, "por identificar"),
        ]
        for extra, mensaje in casos:
            with self.subTest(mensaje):
                datos = {"monto": "300", **extra}
                r = self._pago(datos.pop("monto"), **datos)
                self.assertEqual(r.status_code, 400, r.data)
                self.assertIn(mensaje, str(r.data["aplicaciones"]))
        self.assertFalse(PagoCliente.objects.exists())

    def test_no_se_aplica_mas_de_lo_que_le_queda_a_la_nota(self):
        self.assertEqual(self._pago("10000", aplicaciones=[{"salida_id": self.sal1.id, "monto": "10000"}]).status_code, 201)
        r = self._pago("5000", aplicaciones=[{"salida_id": self.sal1.id, "monto": "4256"}])
        self.assertEqual(r.status_code, 400)
        self.assertIn("le quedan $4,255.00", str(r.data["aplicaciones"]))
        self.assertEqual(self._pago("5000", aplicaciones=[{"salida_id": self.sal1.id, "monto": "4255"}]).status_code, 201)

    def test_editar_y_cancelar_un_pago_aplicado(self):
        r = self._pago("5000", aplicaciones=[{"salida_id": self.sal1.id, "monto": "4000"}])
        pago_id = r.data["id"]
        url = f"/api/cartera/pagos/{pago_id}/"
        # Sin la clave se conserva lo aplicado, pero se revisa si cambia el monto.
        r = self.client.patch(url, {"monto": "3000"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("pasa del monto", str(r.data["aplicaciones"]))
        r = self.client.patch(url, {"notas": "ok"}, format="json")
        self.assertEqual((r.status_code, len(r.data["aplicaciones"]), r.data["editado_por"]["username"]), (200, 1, "cobranza"))
        # Cambiar lo aplicado es una edición.
        r = self.client.patch(url, {"aplicaciones": []}, format="json")
        self.assertEqual((r.status_code, r.data["aplicaciones"]), (200, []))
        self.client.patch(url, {"aplicaciones": [{"salida_id": self.sal1.id, "monto": "4000"}]}, format="json")
        # El pago excluido (el que se edita) no ocupa lo disponible de la nota.
        notas = self.client.get(f"/api/cartera/clientes/{self.cliente.id}/notas-pendientes/",
                                {"empresa": self.imp.id, "pago": pago_id}).data
        self.assertEqual({n["nota"]: n["disponible"] for n in notas}["N-SAL-1"], "14255.00")
        # Cancelado, lo aplicado deja de contar.
        self.client.post(f"{url}cancelar/", {"motivo": "Error"}, format="json")
        self.assertEqual(self._notas(self.imp)["N-SAL-1"]["pendiente"], "14255.00")
        self.assertTrue(AplicacionPago.objects.filter(pago_id=pago_id).exists())

    def test_cargos_y_abonos_en_la_antiguedad(self):
        AjusteCliente.objects.create(cliente=self.cliente, empresa=self.imp, fecha=HOY, monto=Decimal("50"),
                                     naturaleza=AjusteCliente.CARGO, concepto="HORAS EXTRAS", usuario=self.usuario)
        AjusteCliente.objects.create(cliente=self.cliente, empresa=self.imp, fecha=HOY, monto=Decimal("1200"),
                                     naturaleza=AjusteCliente.ABONO, concepto="DESCUENTO", usuario=self.usuario)
        _, bloques = self._bloques()
        imp = bloques["IMPORTADORA"]
        # El abono liquida el saldo al corte y baja 200 de SAL-1; el cargo queda como partida.
        self.assertEqual([(n["nota"], n["pendiente"]) for n in imp["notas"]],
                         [("N-SAL-1", "14055.00"), ("HORAS EXTRAS", "50.00")])
        self.assertEqual(Decimal(imp["saldo"]), saldos_por_cliente([self.cliente.id])[self.cliente.id][self.imp.id])

    def test_saldo_a_favor_y_filtros(self):
        self._pago("20000")
        _, bloques = self._bloques()
        imp = bloques["IMPORTADORA"]
        self.assertEqual((imp["notas"], imp["a_favor"], imp["saldo"]), ([], "4745.00", "-4745.00"))
        datos = self._reporte(empresa=self.sel.id)
        self.assertEqual([b["empresa"]["nombre"] for b in datos["clientes"][0]["empresas"]], ["MARISCOS SELECTOS"])
        self.assertEqual(self.client.get(URL, {"fecha": (HOY + timedelta(days=1)).isoformat()}).status_code, 400)
        self.assertEqual(self.client.get(URL, {"fecha": (CORTE - timedelta(days=1)).isoformat()}).status_code, 400)
        # A la fecha del corte solo está el saldo inicial.
        _, bloques = self._bloques(self._reporte(fecha=CORTE.isoformat()))
        self.assertEqual([n["nota"] for n in bloques["IMPORTADORA"]["notas"]], [f"SALDO AL {CORTE:%d/%m/%Y}"])

    def test_excel_como_cobro_a_clientes(self):
        self._pago("5000", aplicaciones=[{"salida_id": self.sal1.id, "monto": "4000"}])
        libro = load_workbook(BytesIO(self.client.get(f"{URL}excel/").content))
        self.assertEqual(libro.sheetnames, ["RESUMEN", "COBRO A CLIENTES"])
        hoja = libro["COBRO A CLIENTES"]
        self.assertEqual((hoja["C2"].value, hoja["C3"].value, hoja["C4"].value),
                         ("IMPORTADORA", "SALDO A LA FECHA:", "CLIENTE: CABEZA DE TORO"))
        self.assertEqual([hoja.cell(6, c).value for c in range(2, 11)],
                         ["DÍAS DE ANTIGÜEDAD", "NOTA/FACT", "FECHA", "MONTO", "1-15", "16-30", "31-45", "46-89", "90 O MÁS"])
        # Como el área: importe de la nota menos cada pago, y el monto en la columna de su rango.
        self.assertEqual((hoja["B7"].value, hoja["C7"].value, hoja["E7"].value, hoja["F7"].value),
                         ("=$E$3-D7", "N-SAL-1", "=14255.00-4000.00", "=+E7"))
        self.assertEqual((hoja["C8"].value, hoja["E8"].value), ("TOTALES", "=SUM(E7:E7)"))
        # Después del bloque de SELECTOS, el saldo en conjunto.
        conjunto = next(r for r in range(1, hoja.max_row + 1) if hoja.cell(r, 3).value == "SALDO EN CONJUNTO")
        self.assertEqual(hoja.cell(conjunto, 5).value, "=E8+E" + str(conjunto - 2))
        resumen = libro["RESUMEN"]
        self.assertEqual((resumen["A3"].value, resumen["B3"].value, resumen["C3"].value), ("CABEZA DE TORO", "IMPORTADORA", 10255))

    def test_reportes_de_pagos_y_estado_de_cuenta_dicen_a_que_nota_se_aplico(self):
        self._pago("5000", aplicaciones=[{"salida_id": self.sal1.id, "monto": "4000"}])
        libro = load_workbook(BytesIO(self.client.get("/api/cartera/pagos/excel/").content))
        hoja = libro.active
        self.assertEqual(hoja["F1"].value, "Aplicado a")
        self.assertEqual(hoja["F2"].value, "Nota N-SAL-1 $4,000.00; resto a lo más antiguo")
        r = self.client.get(f"/api/cartera/clientes/{self.cliente.id}/estado-cuenta/")
        pago = next(x for x in r.data if x["tipo"] == "pago")
        self.assertIn("Aplicado a: Nota N-SAL-1 $4,000.00", pago["concepto"])
        self.assertEqual(self.client.get("/api/cartera/pagos/pdf/").status_code, 200)
