"""Reporte "Existencias por cámara" (formato docs/MEXIDELI 24.09.26.pdf)."""
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

from django.utils import timezone
from openpyxl import load_workbook
from rest_framework.test import APITestCase

from security.permissions import AREA_INVENTARIO
from security.testing import crear_usuario_con_area

from .models import Camara, Cliente, Empresa, Entrada, EntradaDetalle, Producto, Proveedor, Salida, SalidaDetalle
from .reporte_camaras import construir_reporte
from .testing import empresa_importadora

URL = "/api/inventario/reportes/existencias-camara/"


class ReporteExistenciasCamaraTests(APITestCase):
    def setUp(self):
        self.client.force_authenticate(user=crear_usuario_con_area("almacen", AREA_INVENTARIO))
        self.mexideli = Camara.objects.create(nombre="MEXIDELI", tipo=Camara.TIPO_TERCERO)
        self.importadora = Camara.objects.create(nombre="IMPORTADORA", tipo=Camara.TIPO_PROPIA)
        self.cliente = Cliente.objects.create(nombre="CLIENTE")
        entero = Producto.PRESENTACION_ENTERO
        colas = Producto.PRESENTACION_COLAS
        self.p_30_40 = Producto.objects.create(talla="30-40", tipo="FREEZADO", presentacion=entero)
        self.p_100_120 = Producto.objects.create(talla="100-120", tipo="FREEZADO", presentacion=entero)
        self.p_70_80 = Producto.objects.create(talla="70-80", tipo="MARQUETA", presentacion=entero)
        self.p_41_50 = Producto.objects.create(talla="41-50", tipo="FREEZADO", presentacion=colas)

        self._lote("CACESA", self.p_30_40, 18, 440, "2026-09-01")
        self._lote("MER SEAFOOD", self.p_30_40, 18, 934, "2026-09-01")
        self._lote("ACUAMAYA", self.p_100_120, 20, 10, "2026-09-01")
        agotado = self._lote("BEE GEE", self.p_70_80, 20, 10, "2026-09-01")
        self._salida(agotado, 200, "2026-09-10")  # se vendió completo: no debe aparecer
        colas_lote = self._lote("CACESA", self.p_41_50, 20, 100, "2026-09-01")
        self._salida(colas_lote, 500, "2026-09-20")
        self._lote("CACESA", self.p_30_40, 18, 5, "2026-09-01", camara=self.importadora)

    def _lote(self, proveedor, producto, peso, cajas, fecha, camara=None, empresa=None):
        proveedor, _ = Proveedor.objects.get_or_create(nombre=proveedor)
        entrada = Entrada.objects.create(
            fecha=fecha, proveedor=proveedor, factura=f"F-{Entrada.objects.count()}",
            empresa=empresa or empresa_importadora(),
        )
        return EntradaDetalle.objects.create(
            entrada=entrada, producto=producto, lote_proveedor="L", camara=camara or self.mexideli,
            cajas=cajas, peso_por_caja=Decimal(peso), total_kilos=Decimal(peso * cajas), proveedor_origen=proveedor,
        )

    def _salida(self, lote, kilos, fecha):
        salida = Salida.objects.create(folio_de_salida=f"S-{Salida.objects.count()}", cliente=self.cliente, fecha=fecha)
        SalidaDetalle.objects.create(
            salida=salida, producto=lote.producto, entrada_detalle=lote, camara=lote.camara,
            cajas=int(kilos // lote.peso_por_caja), total_kilos=Decimal(kilos),
        )

    def test_agrupa_por_presentacion_talla_y_proveedor_como_el_formato_del_area(self):
        reporte = construir_reporte(date(2026, 9, 24), camara_id=self.mexideli.id)

        self.assertEqual(len(reporte), 1)
        camara = reporte[0]
        self.assertEqual([b["etiqueta"] for b in camara["bloques"]], ["Entero", "Colas"])
        entero, colas = camara["bloques"]
        # Orden numérico de tallas y talla agotada (70-80) oculta.
        self.assertEqual([t["talla"] for t in entero["tallas"]], ["30-40", "100-120"])
        talla_30_40 = entero["tallas"][0]
        self.assertEqual(talla_30_40["existencia"], Decimal("24732.00"))
        filas = talla_30_40["tipos"][0]["filas"]
        self.assertEqual([(f["proveedor"], f["cajas"], f["kilos"]) for f in filas],
                         [("CACESA", 440, Decimal("7920")), ("MER SEAFOOD", 934, Decimal("16812"))])
        self.assertEqual(entero["subtotal"], Decimal("24932"))
        self.assertEqual(colas["tallas"][0]["tipos"][0]["filas"][0]["cajas"], 75)  # 1500 kg / 20
        self.assertEqual(colas["subtotal"], Decimal("1500"))
        self.assertEqual(camara["total"], Decimal("26432"))

    def test_a_fecha_pasada_no_cuenta_salidas_posteriores(self):
        camara = construir_reporte(date(2026, 9, 15), camara_id=self.mexideli.id)[0]
        colas = camara["bloques"][1]
        self.assertEqual(colas["subtotal"], Decimal("2000"))  # la venta del 20 aún no ocurría
        self.assertNotIn("70-80", [t["talla"] for t in camara["bloques"][0]["tallas"]])  # vendida el 10

        antes_de_todo = construir_reporte(date(2026, 8, 31), camara_id=self.mexideli.id)
        self.assertEqual(antes_de_todo, [])

    def test_sin_camara_trae_una_seccion_por_camara_y_filtra_por_empresa(self):
        selectos = Empresa.objects.get(nombre="MARISCOS SELECTOS")
        self._lote("CACESA", self.p_41_50, 20, 3, "2026-09-02", empresa=selectos)

        todas = construir_reporte(date(2026, 9, 24))
        self.assertEqual([c["camara"]["nombre"] for c in todas], ["IMPORTADORA", "MEXIDELI"])
        solo_selectos = construir_reporte(date(2026, 9, 24), empresa_id=selectos.id)
        self.assertEqual([c["total"] for c in solo_selectos], [Decimal("60")])

    def test_endpoints_json_pdf_y_excel(self):
        params = {"fecha": "2026-09-24", "camara": self.mexideli.id}
        datos = self.client.get(URL, params)
        self.assertEqual(datos.status_code, 200)
        self.assertEqual(datos.data["camaras"][0]["total"], "26432.00")

        pdf = self.client.get(URL + "pdf/", params)
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))

        excel = self.client.get(URL + "excel/", params)
        self.assertEqual(excel.status_code, 200)
        hoja = load_workbook(BytesIO(excel.content))["MEXIDELI"]
        self.assertEqual(hoja["A2"].value, "TALLA")
        # Talla y existencia combinadas a lo alto de sus dos proveedores (fila 4-5, tras el rótulo del bloque).
        rangos = {str(r) for r in hoja.merged_cells.ranges}
        self.assertIn("A4:A5", rangos)
        self.assertIn("G4:G5", rangos)

    def test_fecha_futura_o_invalida_se_rechaza(self):
        manana = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertEqual(self.client.get(URL, {"fecha": manana}).status_code, 400)
        self.assertEqual(self.client.get(URL, {"fecha": "24/09/2026"}).status_code, 400)
