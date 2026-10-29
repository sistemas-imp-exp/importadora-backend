"""Resguardos: venta por producto que el cliente deja guardada y retiros desde Inventario o Cartera."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from notificaciones.models import CanalArea, Notificacion
from security.models import Area
from security.permissions import AREA_CARTERA, AREA_INVENTARIO, AREA_TESORERIA
from security.testing import crear_usuario_con_area

from .models import Camara, Cliente, Entrada, EntradaDetalle, Producto, Proveedor, Salida
from .testing import empresa_importadora

URL = "/api/resguardos/"


class ResguardoTests(APITestCase):
    def setUp(self):
        self.almacen = crear_usuario_con_area("almacen", AREA_INVENTARIO)
        self.cobranza = crear_usuario_con_area("cobranza", AREA_CARTERA)
        CanalArea.objects.get_or_create(
            area_a=Area.objects.get(codigo=AREA_CARTERA), area_b=Area.objects.get(codigo=AREA_INVENTARIO),
        )
        self.cliente = Cliente.objects.create(nombre="HERAY ACERO MORENO")
        self.camara = Camara.objects.create(nombre="CAM-R", tipo=Camara.TIPO_PROPIA)
        self.producto = Producto.objects.create(talla="60-70", tipo="FREEZADO")
        self.otro_producto = Producto.objects.create(talla="70-80", tipo="FREEZADO")
        entrada = Entrada.objects.create(fecha="2026-10-01", empresa=empresa_importadora())
        self.lote_a = self._lote(entrada, "ACUAMAYA", self.producto, 100, "100")
        self.lote_b = self._lote(entrada, "CAMANICA", self.producto, 50, "120")
        self.lote_otro = self._lote(entrada, "CAMANICA", self.otro_producto, 50, "90")

    def _lote(self, entrada, proveedor, producto, cajas, costo):
        return EntradaDetalle.objects.create(
            entrada=entrada, producto=producto, lote_proveedor=f"L-{proveedor}", camara=self.camara, cajas=cajas,
            peso_por_caja=Decimal("20"), total_kilos=Decimal(cajas * 20), costo_por_kilo=Decimal(costo),
            proveedor_origen=Proveedor.objects.get_or_create(nombre=proveedor)[0], factura=f"F-{proveedor}",
        )

    def _vender(self, nota="1559/24B", kilos="1000", cajas=50):
        """Venta en resguardo: por producto, sin lote ni folio de salida."""
        self.client.force_authenticate(user=self.almacen)
        respuesta = self.client.post("/api/inventario/salidas/", {
            "es_resguardo": True, "folio_de_salida": "", "cliente_id": self.cliente.id, "fecha": "2026-10-10",
            "notas": nota,
            "detalles": [{
                "producto_id": self.producto.id, "cajas": cajas, "total_kilos": kilos,
                "precio_x_kilo": "150", "total_venta": str(Decimal(kilos) * 150),
            }],
        }, format="json")
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        return Salida.objects.get(pk=respuesta.data["id"])

    def _retirar(self, venta, kilos="200", cajas=10, lote=None, folio="SI6001", fecha="2026-10-15", linea=None):
        return self.client.post(f"{URL}retiros/", {
            "venta": venta.id, "fecha": fecha, "folio_de_salida": folio, "notas": "1584/24B",
            "detalles": [{
                "retiro_de": (linea or venta.detalles.get()).id, "entrada_detalle": (lote or self.lote_b).id,
                "cajas": cajas, "total_kilos": kilos,
            }],
        }, format="json")

    def _pendiente(self, venta):
        fila = next(r for r in self.client.get(URL, {"estado": "todos"}).data["results"] if r["id"] == venta.id)
        return Decimal(fila["kilos_pendientes"])

    def test_venta_en_resguardo_sin_lote_ni_folio_se_cobra_y_no_toca_existencias(self):
        venta = self._vender()

        self.assertIsNone(venta.folio_de_salida)
        self.assertEqual((venta.es_resguardo, venta.empresa.nombre), (True, "IMPORTADORA"))
        linea = venta.detalles.get()
        self.assertEqual((linea.entrada_detalle, linea.kilos_resguardo, linea.cajas_resguardo), (None, Decimal("1000"), 50))
        self.lote_a.refresh_from_db()
        self.assertEqual(self.lote_a.kilos_disponibles, Decimal("2000"))
        self.assertEqual(self._vender(nota="1560/24B").folio_de_salida, None)  # varias sin folio

        self.client.force_authenticate(user=self.cobranza)
        listado = self.client.get(URL).data
        self.assertEqual((listado["count"], listado["kilos_pendientes"]), (2, "2000.000"))
        # Cartera la cobra completa, en IMPORTADORA.
        from cartera.ventas import empresa_de, importe, lineas_venta
        self.assertEqual({(importe(l), empresa_de(l).nombre) for l in lineas_venta()}, {(Decimal("150000"), "IMPORTADORA")})
        from cartera.saldos import saldos_por_cliente
        self.assertEqual(saldos_por_cliente()[self.cliente.id][venta.empresa_id], Decimal("300000"))

    def test_un_retiro_junta_varios_lotes_y_proveedores_y_avisa(self):
        venta = self._vender()
        self.client.force_authenticate(user=self.cobranza)
        linea = venta.detalles.get()
        respuesta = self.client.post(f"{URL}retiros/", {
            "venta": venta.id, "fecha": "2026-10-15", "folio_de_salida": "SI6001", "notas": "1584/24B",
            "detalles": [
                {"retiro_de": linea.id, "entrada_detalle": self.lote_a.id, "cajas": 30, "total_kilos": "600"},
                {"retiro_de": linea.id, "entrada_detalle": self.lote_b.id, "cajas": 10, "total_kilos": "200"},
            ],
        }, format="json")
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertEqual(respuesta.data["folio_retiro"], "RES-0001")
        self.assertEqual({l["proveedor"] for l in respuesta.data["lineas"]}, {"ACUAMAYA", "CAMANICA"})
        self.lote_a.refresh_from_db()
        self.lote_b.refresh_from_db()
        self.assertEqual((self.lote_a.kilos_disponibles, self.lote_b.kilos_disponibles), (Decimal("1400"), Decimal("800")))
        self.assertEqual(self._pendiente(venta), Decimal("200"))
        aviso = Notificacion.objects.get(evento="resguardo_retiro")
        self.assertEqual((aviso.area_origen.codigo, aviso.area_destino.codigo), ("CAR", "INV"))
        self.assertIn("busqueda=1559/24B", aviso.enlace)

        # El retiro no es otra venta para Cartera; el siguiente lleva el folio que sigue y entrega todo.
        from cartera.ventas import lineas_venta
        self.assertEqual(lineas_venta().count(), 1)
        self.assertEqual(self._retirar(venta, folio="SI6002").data["folio_retiro"], "RES-0002")
        self.assertEqual(self.client.get(URL).data["count"], 0)
        self.assertEqual(self.client.get(URL, {"estado": "entregado"}).data["results"][0]["estado"], "entregado")

    def test_validaciones_del_retiro(self):
        venta = self._vender()
        self.client.force_authenticate(user=self.almacen)
        casos = {
            "más de lo pendiente": self._retirar(venta, kilos="1100", cajas=50),
            "otro producto": self._retirar(venta, lote=self.lote_otro),
            "antes de la venta": self._retirar(venta, fecha="2026-10-01"),
            "más cajas de las que tiene el lote": self._retirar(venta, kilos="1000", cajas=51),
        }
        for caso, respuesta in casos.items():
            with self.subTest(caso):
                self.assertEqual(respuesta.status_code, 400, respuesta.data)
        self._retirar(venta, folio="SI-REP")
        self.assertEqual(self._retirar(venta, folio="SI-REP").status_code, 400)  # folio repetido
        otra = Salida.objects.create(folio_de_salida="SI9", cliente=self.cliente, fecha="2026-10-10", notas="X")
        self.assertEqual(self._retirar(otra, linea=venta.detalles.get()).status_code, 400)

    def test_la_venta_no_baja_de_lo_retirado_ni_se_borra_y_el_retiro_se_cancela_desde_resguardos(self):
        venta = self._vender()
        retiro = self._retirar(venta).data
        linea = venta.detalles.get()

        cambio = {"folio_de_salida": "", "cliente_id": self.cliente.id, "fecha": "2026-10-10", "notas": venta.notas,
                  "detalles": [{"id": linea.id, "producto_id": self.producto.id, "cajas": 5, "total_kilos": "100"}]}
        self.assertEqual(self.client.put(f"/api/inventario/salidas/{venta.id}/", cambio, format="json").status_code, 400)
        cambio["detalles"][0].update(cajas=40, total_kilos="800")
        respuesta = self.client.put(f"/api/inventario/salidas/{venta.id}/", cambio, format="json")
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertTrue(respuesta.data["es_resguardo"])  # sigue siendo venta en resguardo
        self.assertEqual(self.client.delete(f"/api/inventario/salidas/{venta.id}/").status_code, 400)
        self.assertEqual(self.client.delete(f"/api/inventario/salidas/{retiro['id']}/").status_code, 400)

        self.assertEqual(self.client.delete(f"{URL}retiros/{retiro['id']}/").status_code, 204)
        self.assertEqual(self._pendiente(venta), Decimal("800"))
        self.assertTrue(Notificacion.objects.filter(evento="resguardo_retiro_cancelado").exists())
        self.assertEqual(self.client.delete(f"/api/inventario/salidas/{venta.id}/").status_code, 204)

    def test_la_salida_normal_exige_folio_y_lote_y_no_deja_resguardo(self):
        self.client.force_authenticate(user=self.almacen)
        base = {"cliente_id": self.cliente.id, "fecha": "2026-10-10", "notas": "N"}
        linea = {"producto_id": self.producto.id, "entrada_detalle": self.lote_a.id, "cajas": 5, "total_kilos": "100",
                 "kilos_resguardo": "100", "cajas_resguardo": 5}
        sin_folio = self.client.post("/api/inventario/salidas/", {**base, "folio_de_salida": "", "detalles": [linea]}, format="json")
        self.assertIn("folio_de_salida", sin_folio.data)
        sin_lote = {**linea, "entrada_detalle": None}
        self.assertEqual(self.client.post("/api/inventario/salidas/", {**base, "folio_de_salida": "S1", "detalles": [sin_lote]},
                                          format="json").status_code, 400)
        respuesta = self.client.post("/api/inventario/salidas/", {**base, "folio_de_salida": "S1", "detalles": [linea]}, format="json")
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertEqual(respuesta.data["detalles"][0]["kilos_resguardo"], "0.000")  # se ignora
        self.lote_a.refresh_from_db()
        self.assertEqual(self.lote_a.kilos_disponibles, Decimal("1900"))

    def test_utilidad_espera_a_que_se_entregue_todo_y_toma_el_costo_de_cada_lote(self):
        venta = self._vender()
        self._retirar(venta, kilos="400", cajas=20)  # 400 kg del lote B a $120
        jefe = get_user_model().objects.create_superuser("jefe", password="x")
        self.client.force_authenticate(user=jefe)
        params = {"desde": "2026-10-01", "hasta": "2026-10-31"}
        datos = self.client.get("/api/inventario/reportes/utilidad/", params).data
        self.assertEqual(datos["resumen"]["incompletas"], 1)  # lo pendiente aún no tiene costo

        self._retirar(venta, kilos="600", cajas=30, lote=self.lote_a, folio="SI6002")  # 600 kg a $100
        datos = self.client.get("/api/inventario/reportes/utilidad/", params).data
        self.assertEqual(Decimal(datos["resumen"]["costo"]), Decimal("108000.00"))

    def test_lotes_para_el_retiro_y_clientes_del_filtro(self):
        self._vender()
        self.client.force_authenticate(user=self.cobranza)
        lotes = self.client.get(f"{URL}lotes/", {"producto": self.producto.id}).data
        self.assertEqual({l["detalle_id"] for l in lotes}, {self.lote_a.id, self.lote_b.id})
        self.assertEqual(self.client.get(f"{URL}clientes/").data, [{"id": self.cliente.id, "nombre": "HERAY ACERO MORENO"}])

    def test_permisos(self):
        venta = self._vender()
        self.client.force_authenticate(user=crear_usuario_con_area("caja", AREA_TESORERIA))
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.client.force_authenticate(user=crear_usuario_con_area("consulta", AREA_CARTERA, solo_lectura=True))
        self.assertEqual(self.client.get(URL).status_code, 200)
        respuesta = self._retirar(venta)
        self.assertEqual(respuesta.status_code, 403)
        self.assertIn("solo lectura", respuesta.data["detail"])
