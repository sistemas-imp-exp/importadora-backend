"""Resguardos: venta que deja producto guardado y retiros desde Inventario o Cartera."""
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

    def _vender(self, resguardo_kg="600", resguardo_cajas=30):
        self.client.force_authenticate(user=self.almacen)
        respuesta = self.client.post("/api/inventario/salidas/", {
            "folio_de_salida": "SI5349", "cliente_id": self.cliente.id, "fecha": "2026-10-10", "notas": "1559/24B",
            "detalles": [{
                "producto_id": self.producto.id, "entrada_detalle": self.lote_a.id, "cajas": 50,
                "total_kilos": "1000", "precio_x_kilo": "150", "total_venta": "150000",
                "cajas_resguardo": resguardo_cajas, "kilos_resguardo": resguardo_kg,
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

    def test_la_venta_cobra_todo_y_del_lote_solo_sale_lo_entregado(self):
        venta = self._vender()

        self.lote_a.refresh_from_db()
        self.assertEqual(self.lote_a.kilos_disponibles, Decimal("1600"))  # 2000 − (1000 − 600)
        self.client.force_authenticate(user=self.cobranza)
        listado = self.client.get(URL).data
        self.assertEqual((listado["count"], listado["kilos_pendientes"]), (1, "600.000"))
        self.assertEqual(listado["results"][0]["lineas"][0]["kilos_pendientes"], "600.000")
        # Cartera cobra la venta completa.
        from cartera.ventas import importe, lineas_venta
        self.assertEqual([importe(l) for l in lineas_venta()], [Decimal("150000")])
        self.assertEqual(Salida.objects.get(pk=venta.pk).detalles.get().kilos_resguardo, Decimal("600"))

    def test_cartera_registra_un_retiro_de_otro_proveedor_y_avisa_a_inventario(self):
        venta = self._vender()
        self.client.force_authenticate(user=self.cobranza)

        respuesta = self._retirar(venta)
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertEqual((respuesta.data["folio_retiro"], respuesta.data["lineas"][0]["proveedor"]), ("RES-0001", "CAMANICA"))
        self.lote_b.refresh_from_db()
        self.assertEqual(self.lote_b.kilos_disponibles, Decimal("800"))
        self.assertEqual(self._pendiente(venta), Decimal("400"))
        aviso = Notificacion.objects.get(evento="resguardo_retiro")
        self.assertEqual((aviso.area_origen.codigo, aviso.area_destino.codigo), ("CAR", "INV"))

        # El retiro no es otra venta para Cartera; el siguiente lleva el folio que sigue.
        from cartera.ventas import lineas_venta
        self.assertEqual(lineas_venta().count(), 1)
        self.assertEqual(self._retirar(venta, folio="SI6002", lote=self.lote_a).data["folio_retiro"], "RES-0002")
        self.assertEqual(self._pendiente(venta), Decimal("200"))

        # Entregado completo: deja de estar pendiente.
        self._retirar(venta, folio="SI6003")
        self.assertEqual(self.client.get(URL).data["count"], 0)
        self.assertEqual(self.client.get(URL, {"estado": "entregado"}).data["results"][0]["estado"], "entregado")

    def test_validaciones_del_retiro(self):
        venta = self._vender()
        self.client.force_authenticate(user=self.almacen)
        casos = {
            "más de lo pendiente": self._retirar(venta, kilos="700"),
            "otro producto": self._retirar(venta, lote=self.lote_otro),
            "antes de la venta": self._retirar(venta, fecha="2026-10-01"),
            "folio repetido": self._retirar(venta, folio="SI5349"),
        }
        for caso, respuesta in casos.items():
            with self.subTest(caso):
                self.assertEqual(respuesta.status_code, 400, respuesta.data)
        otra = Salida.objects.create(folio_de_salida="SI9", cliente=self.cliente, fecha="2026-10-10", notas="X")
        self.assertEqual(self._retirar(otra, linea=venta.detalles.get()).status_code, 400)

    def test_la_venta_no_baja_de_lo_retirado_ni_se_borra_y_el_retiro_se_cancela_desde_resguardos(self):
        venta = self._vender()
        retiro = self._retirar(venta).data
        linea = venta.detalles.get()

        payload = self.client.get(f"/api/inventario/salidas/{venta.id}/").data
        detalle = {
            "id": linea.id, "producto_id": self.producto.id, "entrada_detalle": self.lote_a.id, "cajas": 50,
            "total_kilos": "1000", "kilos_resguardo": "100", "cajas_resguardo": 5,
        }
        cambio = {"folio_de_salida": payload["folio_de_salida"], "cliente_id": self.cliente.id,
                  "fecha": payload["fecha"], "notas": payload["notas"], "detalles": [detalle]}
        self.assertEqual(self.client.put(f"/api/inventario/salidas/{venta.id}/", cambio, format="json").status_code, 400)
        self.assertEqual(self.client.delete(f"/api/inventario/salidas/{venta.id}/").status_code, 400)
        self.assertEqual(self.client.delete(f"/api/inventario/salidas/{retiro['id']}/").status_code, 400)

        self.assertEqual(self.client.delete(f"{URL}retiros/{retiro['id']}/").status_code, 204)
        self.assertEqual(self._pendiente(venta), Decimal("600"))
        self.assertTrue(Notificacion.objects.filter(evento="resguardo_retiro_cancelado").exists())
        self.assertEqual(self.client.delete(f"/api/inventario/salidas/{venta.id}/").status_code, 204)

    def _venta_sin_folio(self, nota, resguardo_kg="1000", resguardo_cajas=50):
        self.client.force_authenticate(user=self.almacen)
        return self.client.post("/api/inventario/salidas/", {
            "folio_de_salida": "", "cliente_id": self.cliente.id, "fecha": "2026-10-10", "notas": nota,
            "detalles": [{
                "producto_id": self.producto.id, "entrada_detalle": self.lote_a.id, "cajas": 50,
                "total_kilos": "1000", "precio_x_kilo": "150", "total_venta": "150000",
                "cajas_resguardo": resguardo_cajas, "kilos_resguardo": resguardo_kg,
            }],
        }, format="json")

    def test_venta_completa_en_resguardo_no_lleva_folio_de_salida(self):
        respuesta = self._venta_sin_folio("2001/26B")
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertIsNone(respuesta.data["folio_de_salida"])
        # Varias ventas pueden quedarse sin folio (el folio es único solo cuando existe).
        self.assertEqual(self._venta_sin_folio("2002/26B").status_code, 201)
        self.lote_a.refresh_from_db()
        self.assertEqual(self.lote_a.kilos_disponibles, Decimal("2000"))  # no salió nada

        venta = Salida.objects.get(notas="2001/26B")
        self.assertEqual(venta.clave_busqueda, "2001/26B")
        fila = self.client.get(URL, {"busqueda": "2001/26B"}).data["results"][0]
        self.assertEqual((fila["folio_de_salida"], fila["kilos_pendientes"]), (None, "1000.000"))
        self.client.force_authenticate(user=self.cobranza)
        self.assertEqual(self._retirar(venta, kilos="200").status_code, 201)
        aviso = Notificacion.objects.get(evento="resguardo_retiro")
        self.assertIn("busqueda=2001/26B", aviso.enlace)

    def test_si_sale_algo_el_folio_es_obligatorio(self):
        respuesta = self._venta_sin_folio("2003/26B", resguardo_kg="600", resguardo_cajas=30)
        self.assertEqual(respuesta.status_code, 400, respuesta.data)
        self.assertIn("folio_de_salida", respuesta.data)

    def test_resguardo_mayor_a_lo_vendido_se_rechaza(self):
        self.client.force_authenticate(user=self.almacen)
        respuesta = self.client.post("/api/inventario/salidas/", {
            "folio_de_salida": "SI1", "cliente_id": self.cliente.id, "fecha": "2026-10-10", "notas": "N",
            "detalles": [{"producto_id": self.producto.id, "entrada_detalle": self.lote_a.id, "cajas": 5,
                          "total_kilos": "100", "kilos_resguardo": "150"}],
        }, format="json")
        self.assertEqual(respuesta.status_code, 400, respuesta.data)

    def test_utilidad_toma_el_costo_del_lote_de_cada_retiro(self):
        venta = self._vender()
        self._retirar(venta)  # 200 kg del lote B a $120
        self.client.force_authenticate(user=get_user_model().objects.create_superuser("jefe", password="x"))
        datos = self.client.get("/api/inventario/reportes/utilidad/", {"desde": "2026-10-01", "hasta": "2026-10-31"}).data
        # Entregado 400 + pendiente 400 a $100 del lote A, más 200 a $120 del lote B.
        self.assertEqual(Decimal(datos["resumen"]["costo"]), Decimal("104000.00"))
        self.assertEqual(datos["resumen"]["lineas"], 1)

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
