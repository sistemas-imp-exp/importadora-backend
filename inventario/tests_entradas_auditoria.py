"""
Pruebas de los cambios de Entradas: bloqueo de edición/borrado cuando ya hay
salidas, bitácora de ediciones y vía restringida de superusuario.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import (
    Camara,
    Cliente,
    EdicionEntrada,
    Entrada,
    EntradaDetalle,
    Producto,
    Proveedor,
    Salida,
    SalidaDetalle,
)


class EntradaBloqueoConSalidasApiTests(APITestCase):
    """Una entrada que ya tuvo salidas no se edita ni se borra por la vía normal."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="bloqueo", password="x")
        self.client.force_authenticate(user=self.user)
        self.proveedor = Proveedor.objects.create(nombre="CACESA")
        self.cliente = Cliente.objects.create(nombre="HERAY")
        self.camara = Camara.objects.create(nombre="CAM-BLOQ", tipo=Camara.TIPO_PROPIA)
        self.producto = Producto.objects.create(talla="21-25", tipo="FREEZADO")

        self.entrada = Entrada.objects.create(
            fecha="2026-03-01", proveedor=self.proveedor, factura="FACT-BLOQ"
        )
        self.lote = EntradaDetalle.objects.create(
            entrada=self.entrada, producto=self.producto, lote_proveedor="LOTE-BLOQ",
            camara=self.camara, cajas=100, peso_por_caja=Decimal("18.00"),
            total_kilos=Decimal("1800.00"),
            proveedor_origen=self.proveedor,
        )

    def _payload(self, **cambios):
        payload = {
            "fecha": "2026-03-01",
            "proveedor_id": self.proveedor.id,
            "factura": "FACT-BLOQ",
            "pedimento": "",
            "detalles": [{
                "id": self.lote.id,
                "producto_id": self.producto.id,
                "lote_proveedor": "LOTE-BLOQ",
                "camara": self.camara.id,
                "cajas": 100,
                "peso_por_caja": "18.00",
                "total_kilos": "1800.00",
            }],
        }
        payload.update(cambios)
        return payload

    def _registrar_salida(self, cajas=40):
        salida = Salida.objects.create(
            folio_de_salida=f"SAL-{cajas}", cliente=self.cliente, fecha="2026-03-05"
        )
        SalidaDetalle.objects.create(
            salida=salida, producto=self.producto, entrada_detalle=self.lote,
            camara=self.camara, cajas=cajas, total_kilos=Decimal("720.00"),
        )
        return salida

    def test_editar_entrada_sin_salidas_funciona(self):
        response = self.client.put(
            f"/api/inventario/entradas/{self.entrada.id}/",
            self._payload(factura="FACT-NUEVA"),
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.entrada.refresh_from_db()
        self.assertEqual(self.entrada.factura, "FACT-NUEVA")

    def test_editar_entrada_con_salidas_se_rechaza(self):
        self._registrar_salida()

        response = self.client.put(
            f"/api/inventario/entradas/{self.entrada.id}/",
            self._payload(factura="FACT-HACKEADA"),
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.entrada.refresh_from_db()
        self.assertEqual(self.entrada.factura, "FACT-BLOQ")

    def test_no_se_puede_bajar_cajas_por_debajo_de_lo_vendido(self):
        """El caso que dejaba cajas_disponibles en negativo antes del bloqueo."""
        self._registrar_salida(cajas=40)

        payload = self._payload()
        payload["detalles"][0]["cajas"] = 5
        response = self.client.put(f"/api/inventario/entradas/{self.entrada.id}/", payload, format="json")

        self.assertEqual(response.status_code, 400, response.data)
        self.lote.refresh_from_db()
        self.assertEqual(self.lote.cajas, 100)
        self.assertEqual(self.lote.cajas_disponibles, 60)

    def test_eliminar_entrada_con_salidas_se_rechaza(self):
        self._registrar_salida()

        response = self.client.delete(f"/api/inventario/entradas/{self.entrada.id}/")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(Entrada.objects.filter(id=self.entrada.id).exists())

    def test_eliminar_entrada_sin_salidas_borra_lineas_y_resta_del_inventario(self):
        response = self.client.delete(f"/api/inventario/entradas/{self.entrada.id}/")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Entrada.objects.filter(id=self.entrada.id).exists())
        self.assertFalse(EntradaDetalle.objects.filter(id=self.lote.id).exists())

    def test_eliminar_entrada_editada_no_choca_con_la_bitacora(self):
        """La bitácora no debe impedir la baja: sus FK son SET_NULL."""
        self.client.put(
            f"/api/inventario/entradas/{self.entrada.id}/",
            self._payload(factura="FACT-EDITADA"),
            format="json",
        )
        self.assertTrue(EdicionEntrada.objects.filter(entrada=self.entrada).exists())

        response = self.client.delete(f"/api/inventario/entradas/{self.entrada.id}/")

        self.assertEqual(response.status_code, 204)
        # El historial sobrevive a la entrada borrada, con su referencia legible.
        registros = EdicionEntrada.objects.all()
        self.assertTrue(registros.exists())
        for registro in registros:
            self.assertIsNone(registro.entrada_id)
            self.assertIn("FACT", registro.entrada_referencia)


class BitacoraEdicionEntradaApiTests(APITestCase):
    """Toda edición normal deja rastro de qué se movió, quién y cuándo."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="capturista", password="x")
        self.client.force_authenticate(user=self.user)
        self.proveedor = Proveedor.objects.create(nombre="ACUAMAYA")
        self.camara = Camara.objects.create(nombre="CAM-BIT", tipo=Camara.TIPO_PROPIA)
        self.producto = Producto.objects.create(talla="26-30", tipo="MARQUETA")
        self.otro_producto = Producto.objects.create(talla="31-35", tipo="MARQUETA")

        self.entrada = Entrada.objects.create(
            fecha="2026-04-01", proveedor=self.proveedor, factura="FACT-BIT"
        )
        self.lote = EntradaDetalle.objects.create(
            entrada=self.entrada, producto=self.producto, lote_proveedor="LOTE-BIT",
            camara=self.camara, cajas=20, peso_por_caja=Decimal("18.00"),
            total_kilos=Decimal("360.00"),
            proveedor_origen=self.proveedor,
        )

    def _linea(self, **cambios):
        linea = {
            "id": self.lote.id,
            "producto_id": self.producto.id,
            "lote_proveedor": "LOTE-BIT",
            "camara": self.camara.id,
            "cajas": 20,
            "peso_por_caja": "18.00",
            "total_kilos": "360.00",
        }
        linea.update(cambios)
        return linea

    def _put(self, detalles, **cabecera):
        payload = {
            "fecha": "2026-04-01",
            "proveedor_id": self.proveedor.id,
            "factura": "FACT-BIT",
            "pedimento": "",
            "detalles": detalles,
        }
        payload.update(cabecera)
        return self.client.put(f"/api/inventario/entradas/{self.entrada.id}/", payload, format="json")

    def test_cambio_de_cabecera_queda_registrado_con_usuario(self):
        response = self._put([self._linea()], factura="FACT-CORREGIDA")

        self.assertEqual(response.status_code, 200, response.data)
        registro = EdicionEntrada.objects.get(campo="factura")
        self.assertEqual(registro.valor_anterior, "FACT-BIT")
        self.assertEqual(registro.valor_nuevo, "FACT-CORREGIDA")
        self.assertEqual(registro.editado_por, self.user)
        self.assertIsNotNone(registro.editado_en)

    def test_cambio_de_linea_registra_valores_legibles_no_ids(self):
        response = self._put([self._linea(producto_id=self.otro_producto.id, cajas=25)])

        self.assertEqual(response.status_code, 200, response.data)
        cambio_producto = EdicionEntrada.objects.get(campo="producto")
        self.assertEqual(cambio_producto.valor_anterior, str(self.producto))
        self.assertEqual(cambio_producto.valor_nuevo, str(self.otro_producto))
        self.assertTrue(EdicionEntrada.objects.filter(campo="cajas", valor_nuevo="25").exists())

    def test_linea_agregada_y_eliminada_quedan_registradas(self):
        nueva = {
            "producto_id": self.otro_producto.id,
            "lote_proveedor": "LOTE-NUEVO",
            "camara": self.camara.id,
            "cajas": 7,
            "peso_por_caja": "18.00",
            "total_kilos": "126.00",
        }
        self.assertEqual(self._put([self._linea(), nueva]).status_code, 200)
        self.assertTrue(EdicionEntrada.objects.filter(campo="linea_agregada").exists())

        # Ahora se quita la línea original y queda solo la nueva.
        agregada = self.entrada.detalles.exclude(id=self.lote.id).get()
        respuesta = self._put([{
            "id": agregada.id,
            "producto_id": self.otro_producto.id,
            "lote_proveedor": "LOTE-NUEVO",
            "camara": self.camara.id,
            "cajas": 7,
            "peso_por_caja": "18.00",
            "total_kilos": "126.00",
        }])

        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        eliminada = EdicionEntrada.objects.get(campo="linea_eliminada")
        self.assertIn("LOTE-BIT", eliminada.linea_referencia)

    def test_guardar_sin_cambios_no_ensucia_la_bitacora(self):
        self.assertEqual(self._put([self._linea()]).status_code, 200)

        self.assertEqual(EdicionEntrada.objects.count(), 0)

    def test_eliminar_entrada_deja_constancia_de_la_baja(self):
        self.client.delete(f"/api/inventario/entradas/{self.entrada.id}/")

        registro = EdicionEntrada.objects.get(campo="entrada_eliminada")
        self.assertIsNone(registro.entrada_id)
        self.assertIn("FACT-BIT", registro.entrada_referencia)
        self.assertIn("LOTE-BIT", registro.valor_anterior)
        self.assertEqual(registro.editado_por, self.user)

    def test_bandera_editado_del_serializer(self):
        # El listado va paginado en el servidor: {count, next, previous, results}.
        listado = self.client.get("/api/inventario/entradas/").data
        self.assertEqual(listado["count"], 1)
        self.assertFalse(listado["results"][0]["editado"])

        self._put([self._linea()], factura="OTRA-FACT")

        listado = self.client.get("/api/inventario/entradas/").data
        self.assertTrue(listado["results"][0]["editado"])

    def test_busqueda_filtra_en_el_servidor(self):
        self.assertEqual(self.client.get("/api/inventario/entradas/?busqueda=FACT-BIT").data["count"], 1)
        self.assertEqual(self.client.get("/api/inventario/entradas/?busqueda=LOTE-BIT").data["count"], 1)
        self.assertEqual(self.client.get("/api/inventario/entradas/?busqueda=NO-EXISTE").data["count"], 0)

    def test_resumen_no_incluye_lotes(self):
        resumen = self.client.get("/api/inventario/entradas/resumen/").data

        self.assertEqual(len(resumen), 1)
        self.assertEqual(set(resumen[0]), {"id", "fecha", "proveedor", "factura", "editado"})


class AuditoriaEdicionRestringidaApiTests(APITestCase):
    """Vía de superusuario: corrige solo lo que no altera la contabilidad."""

    def setUp(self):
        self.superusuario = get_user_model().objects.create_superuser(
            username="jefe", password="x", email="jefe@example.com"
        )
        self.normal = get_user_model().objects.create_user(username="normalito", password="x")
        self.proveedor = Proveedor.objects.create(nombre="MANTABAY")
        self.cliente = Cliente.objects.create(nombre="CLIENTE-AUD")
        self.camara = Camara.objects.create(nombre="CAM-AUD", tipo=Camara.TIPO_PROPIA)
        self.producto = Producto.objects.create(talla="36-40", tipo="FREEZADO")

        self.entrada = Entrada.objects.create(
            fecha="2026-05-01", proveedor=self.proveedor, factura="FACT-AUD"
        )
        self.lote = EntradaDetalle.objects.create(
            entrada=self.entrada, producto=self.producto, lote_proveedor="LOTE-AUD",
            camara=self.camara, cajas=50, peso_por_caja=Decimal("18.00"),
            total_kilos=Decimal("900.00"),
            proveedor_origen=self.proveedor,
        )
        salida = Salida.objects.create(
            folio_de_salida="SAL-AUD", cliente=self.cliente, fecha="2026-05-02"
        )
        SalidaDetalle.objects.create(
            salida=salida, producto=self.producto, entrada_detalle=self.lote,
            camara=self.camara, cajas=10, total_kilos=Decimal("180.00"),
        )
        self.url = f"/api/inventario/auditoria/entradas/{self.entrada.id}/"

    def test_usuario_normal_no_entra(self):
        self.client.force_authenticate(user=self.normal)

        self.assertEqual(self.client.get("/api/inventario/auditoria/entradas/").status_code, 403)
        respuesta = self.client.patch(
            self.url, {"motivo": "x", "cabecera": {"factura": "Y"}}, format="json"
        )
        self.assertEqual(respuesta.status_code, 403)

    def test_superusuario_corrige_campo_permitido_pese_a_tener_salidas(self):
        self.client.force_authenticate(user=self.superusuario)

        response = self.client.patch(
            self.url,
            {"motivo": "Factura mal capturada", "cabecera": {"factura": "FACT-REAL"}},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.entrada.refresh_from_db()
        self.assertEqual(self.entrada.factura, "FACT-REAL")
        registro = EdicionEntrada.objects.get(campo="factura")
        self.assertEqual(registro.motivo, "Factura mal capturada")
        self.assertEqual(registro.editado_por, self.superusuario)

    def test_campo_que_altera_la_contabilidad_se_rechaza(self):
        self.client.force_authenticate(user=self.superusuario)

        for campo, valor in [("cajas", 5), ("camara", 99), ("costo_por_kilo", "1.00")]:
            with self.subTest(campo=campo):
                response = self.client.patch(
                    self.url,
                    {"motivo": "intento", "lineas": [{"id": self.lote.id, campo: valor}]},
                    format="json",
                )
                self.assertEqual(response.status_code, 400, response.data)

        self.lote.refresh_from_db()
        self.assertEqual(self.lote.cajas, 50)
        self.assertEqual(EdicionEntrada.objects.count(), 0)

    def test_motivo_es_obligatorio(self):
        self.client.force_authenticate(user=self.superusuario)

        response = self.client.patch(self.url, {"cabecera": {"factura": "SIN-MOTIVO"}}, format="json")

        self.assertEqual(response.status_code, 400, response.data)
        self.entrada.refresh_from_db()
        self.assertEqual(self.entrada.factura, "FACT-AUD")

    def test_corregir_linea_permitida_y_leer_la_bitacora(self):
        self.client.force_authenticate(user=self.superusuario)

        response = self.client.patch(
            self.url,
            {
                "motivo": "Caducidad faltante",
                "lineas": [{
                    "id": self.lote.id,
                    "fecha_caducidad": "2026-12-31",
                    "observaciones": "revisado",
                }],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)

        self.lote.refresh_from_db()
        self.assertEqual(str(self.lote.fecha_caducidad), "2026-12-31")
        self.assertEqual(self.lote.observaciones, "REVISADO")

        bitacora = self.client.get("/api/inventario/auditoria/entradas/").data
        campos = {r["campo"] for r in bitacora["registros"]}
        self.assertEqual(campos, {"fecha_caducidad", "observaciones"})
        self.assertTrue(all(r["entrada_existe"] for r in bitacora["registros"]))
