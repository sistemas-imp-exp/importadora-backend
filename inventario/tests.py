import importlib
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from security.permissions import AREA_INVENTARIO
from security.testing import crear_usuario_con_area

from .alertas import clasificar_nivel, obtener_lotes_por_vencer
from .models import Camara, Cliente, Empresa, Proveedor, Producto, Entrada, EntradaDetalle, LoteGeneral, Salida, SalidaDetalle, MovimientoCamara


class CatalogosModelTests(TestCase):
    def test_camara_propia_tiene_empresa_y_camara_de_tercero_no(self):
        empresa = Empresa.objects.create(nombre="Importadora y Exportadora de Mariscos")
        propia = Camara.objects.create(
            nombre="IMPORTADORA", ubicacion="Arriaga", tipo="propia", empresa=empresa
        )
        tercero = Camara.objects.create(
            nombre="REMAINS3", ubicacion="Tapachula", tipo="tercero"
        )

        self.assertEqual(propia.empresa, empresa)
        self.assertIsNone(tercero.empresa)
        self.assertEqual(str(propia), "IMPORTADORA")

    def test_proveedor_y_cliente_solo_requieren_nombre(self):
        proveedor = Proveedor.objects.create(nombre="CACESA")
        cliente = Cliente.objects.create(nombre="HERAY ACERO MORENO")

        self.assertEqual(str(proveedor), "CACESA")
        self.assertEqual(str(cliente), "HERAY ACERO MORENO")


class ProductoModelTests(TestCase):
    def test_talla_y_tipo_no_se_repiten(self):
        Producto.objects.create(talla="41-50", tipo="FREEZADO", categoria="Camarón")

        with self.assertRaises(IntegrityError):
            Producto.objects.create(talla="41-50", tipo="FREEZADO")

    def test_presentacion_es_opcional_y_no_se_deriva_de_la_talla(self):
        producto = Producto.objects.create(
            talla="16-20", tipo="MARQUETA", presentacion=Producto.PRESENTACION_COLAS
        )

        self.assertEqual(producto.presentacion, Producto.PRESENTACION_COLAS)
        self.assertEqual(str(producto), "16-20 MARQUETA")


class EntradaModelTests(TestCase):
    def setUp(self):
        self.proveedor = Proveedor.objects.create(nombre="CACESA")
        self.camara = Camara.objects.create(nombre="IMPORTADORA", tipo=Camara.TIPO_PROPIA)
        self.producto = Producto.objects.create(talla="30-40", tipo="FREEZADO")

    def test_entrada_detalle_a_resguardo_requiere_lote_proveedor_y_puede_agrupar_en_lote_general(self):
        entrada = Entrada.objects.create(
            fecha="2026-01-05", proveedor=self.proveedor, factura="FACT 1070"
        )
        lote_general = LoteGeneral.objects.create(
            codigo="IMP1765", entrada=entrada, camara=self.camara, fecha_recibo="2026-01-05"
        )
        detalle = EntradaDetalle.objects.create(
            entrada=entrada,
            producto=self.producto,
            lote_general=lote_general,
            lote_proveedor="LOTE-001",
            camara=self.camara,
            cajas=1,
            peso_por_caja=Decimal("18.00"),
            total_kilos=Decimal("18.00"),
            costo_por_kilo=Decimal("100.00"),
            precio_venta_planeado=Decimal("150.00"),
        )

        self.assertEqual(detalle.lote_general.codigo, "IMP1765")
        self.assertEqual(detalle.entrada.proveedor, self.proveedor)

    def test_entrada_detalle_de_venta_directa_no_requiere_lote_general_ni_camara(self):
        entrada = Entrada.objects.create(fecha="2026-01-02", proveedor=self.proveedor)
        detalle = EntradaDetalle.objects.create(
            entrada=entrada,
            producto=self.producto,
            lote_proveedor="LOTE-DIRECTO",
            cajas=25,
            peso_por_caja=Decimal("20.00"),
            total_kilos=Decimal("500.00"),
        )

        self.assertIsNone(detalle.lote_general)
        self.assertIsNone(detalle.camara)
        self.assertIsNone(detalle.costo_por_kilo)

    def test_lote_proveedor_vacio_es_rechazado_por_la_base_de_datos(self):
        entrada = Entrada.objects.create(fecha="2026-01-02", proveedor=self.proveedor)

        with self.assertRaises(IntegrityError):
            EntradaDetalle.objects.create(
                entrada=entrada,
                producto=self.producto,
                lote_proveedor="",
                cajas=10,
                peso_por_caja=Decimal("10.00"),
                total_kilos=Decimal("100.00"),
            )


class CorregirPesoPorCajaMigracionTests(TestCase):
    """La 0018 rellena los peso_por_caja que la 0017 dejó en 0."""

    def test_deriva_peso_de_total_kilos_entre_cajas(self):
        from django.apps import apps
        migracion = importlib.import_module("inventario.migrations.0018_corregir_peso_por_caja")

        proveedor = Proveedor.objects.create(nombre="CACESA")
        producto = Producto.objects.create(talla="30-40", tipo="FREEZADO")
        entrada = Entrada.objects.create(fecha="2026-01-05", proveedor=proveedor)
        con_cajas = EntradaDetalle.objects.create(
            entrada=entrada, producto=producto, lote_proveedor="LOTE-1",
            cajas=3, peso_por_caja=Decimal("1.00"), total_kilos=Decimal("100.00"),
        )
        sin_cajas = EntradaDetalle.objects.create(
            entrada=entrada, producto=producto, lote_proveedor="LOTE-2",
            cajas=0, peso_por_caja=Decimal("1.00"), total_kilos=Decimal("7.50"),
        )
        # update() salta el validador, igual que el default=0 de la 0017.
        EntradaDetalle.objects.update(peso_por_caja=Decimal("0"))

        migracion.corregir_peso_por_caja(apps, None)

        con_cajas.refresh_from_db()
        sin_cajas.refresh_from_db()
        self.assertEqual(con_cajas.peso_por_caja, Decimal("33.33"))
        self.assertEqual(sin_cajas.peso_por_caja, Decimal("7.50"))


class SalidaModelTests(TestCase):
    def setUp(self):
        self.proveedor = Proveedor.objects.create(nombre="CACESA")
        self.cliente = Cliente.objects.create(nombre="HERAY ACERO MORENO")
        self.camara = Camara.objects.create(nombre="IMPORTADORA", tipo=Camara.TIPO_PROPIA)
        self.producto = Producto.objects.create(talla="41-50", tipo="FREEZADO")

        self.entrada = Entrada.objects.create(fecha="2026-01-05", proveedor=self.proveedor)
        self.lote_a = EntradaDetalle.objects.create(
            entrada=self.entrada,
            producto=self.producto,
            lote_proveedor="LOTE-A",
            camara=self.camara,
            cajas=60,
            peso_por_caja=Decimal("18.00"),
            total_kilos=Decimal("1080.00"),
        )
        self.lote_b = EntradaDetalle.objects.create(
            entrada=self.entrada,
            producto=self.producto,
            lote_proveedor="LOTE-B",
            camara=self.camara,
            cajas=40,
            peso_por_caja=Decimal("18.00"),
            total_kilos=Decimal("720.00"),
        )

    def test_venta_que_se_completa_con_dos_lotes_se_parte_en_dos_lineas(self):
        salida = Salida.objects.create(
            folio_de_salida="SI9000", cliente=self.cliente, fecha="2026-02-01"
        )
        SalidaDetalle.objects.create(
            salida=salida,
            producto=self.producto,
            entrada_detalle=self.lote_a,
            camara=self.camara,
            cajas=60,
            total_kilos=Decimal("1080.00"),
            factura_proveedor="FACT 1070",
            precio_x_kilo=Decimal("140.00"),
        )
        SalidaDetalle.objects.create(
            salida=salida,
            producto=self.producto,
            entrada_detalle=self.lote_b,
            camara=self.camara,
            cajas=40,
            total_kilos=Decimal("720.00"),
            factura_proveedor="FACT 1090",
            precio_x_kilo=Decimal("145.00"),
        )

        self.assertEqual(salida.detalles.count(), 2)
        facturas = set(salida.detalles.values_list('factura_proveedor', flat=True))
        self.assertEqual(facturas, {"FACT 1070", "FACT 1090"})

    def test_venta_parcial_descuenta_cajas_y_kilos_disponibles_del_lote(self):
        # lote_a entró con 60 cajas / 1080.00 kilos (18kg/caja)
        salida = Salida.objects.create(
            folio_de_salida="SI9100", cliente=self.cliente, fecha="2026-02-02"
        )
        SalidaDetalle.objects.create(
            salida=salida,
            producto=self.producto,
            entrada_detalle=self.lote_a,
            camara=self.camara,
            cajas=20,
            total_kilos=Decimal("360.00"),
            precio_x_kilo=Decimal("140.00"),
        )

        self.lote_a.refresh_from_db()
        self.assertEqual(self.lote_a.cajas_disponibles, 40)
        self.assertEqual(self.lote_a.kilos_disponibles, Decimal("720.00"))
        # el lote que no tuvo venta no se ve afectado
        self.assertEqual(self.lote_b.cajas_disponibles, 40)
        self.assertEqual(self.lote_b.kilos_disponibles, Decimal("720.00"))


class MovimientoCamaraModelTests(TestCase):
    def test_movimiento_enlaza_salida_origen_con_entrada_destino_sin_datos_comerciales(self):
        proveedor = Proveedor.objects.create(nombre="CACESA")
        cliente = Cliente.objects.create(nombre="IMPORTADORA-MEXIDELI")
        camara_origen = Camara.objects.create(nombre="IMPORTADORA", tipo=Camara.TIPO_PROPIA)
        camara_destino = Camara.objects.create(nombre="MEXIDELI", tipo=Camara.TIPO_PROPIA)
        producto = Producto.objects.create(talla="31-35", tipo="FREEZADO")

        entrada_origen = Entrada.objects.create(fecha="2026-01-20", proveedor=proveedor)
        lote_origen = EntradaDetalle.objects.create(
            entrada=entrada_origen,
            producto=producto,
            lote_proveedor="LOTE-ORIGEN",
            camara=camara_origen,
            cajas=500,
            peso_por_caja=Decimal("20.00"),
            total_kilos=Decimal("10000.00"),
        )

        salida = Salida.objects.create(
            folio_de_salida="SI8085", cliente=cliente, fecha="2026-01-26"
        )
        salida_detalle = SalidaDetalle.objects.create(
            salida=salida,
            producto=producto,
            entrada_detalle=lote_origen,
            camara=camara_origen,
            cajas=500,
            total_kilos=Decimal("10000.00"),
        )

        entrada_destino = Entrada.objects.create(fecha="2026-01-26", proveedor=proveedor)
        lote_destino = EntradaDetalle.objects.create(
            entrada=entrada_destino,
            producto=producto,
            lote_proveedor="LOTE-ORIGEN",
            camara=camara_destino,
            cajas=500,
            peso_por_caja=Decimal("20.00"),
            total_kilos=Decimal("10000.00"),
        )

        movimiento = MovimientoCamara.objects.create(
            entrada_detalle_origen=lote_origen,
            salida_detalle=salida_detalle,
            entrada_detalle_destino=lote_destino,
            camara_origen=camara_origen,
            camara_destino=camara_destino,
            fecha="2026-01-26",
            cajas=500,
        )

        self.assertEqual(salida_detalle.movimiento_camara, movimiento)
        self.assertEqual(lote_destino.movimiento_camara_como_destino, movimiento)

    def test_una_salida_puede_mover_dos_lotes_distintos_a_la_vez(self):
        proveedor = Proveedor.objects.create(nombre="CACESA")
        cliente = Cliente.objects.create(nombre="IMPORTADORA-MEXIDELI")
        camara_origen = Camara.objects.create(nombre="IMPORTADORA2", tipo=Camara.TIPO_PROPIA)
        camara_destino = Camara.objects.create(nombre="MEXIDELI2", tipo=Camara.TIPO_PROPIA)
        producto = Producto.objects.create(talla="61-70", tipo="FREEZADO")

        entrada = Entrada.objects.create(fecha="2026-01-20", proveedor=proveedor)
        lote_1 = EntradaDetalle.objects.create(
            entrada=entrada, producto=producto, lote_proveedor="LOTE-1",
            camara=camara_origen, cajas=100, peso_por_caja=Decimal("10.00"),
            total_kilos=Decimal("1000.00"),
        )
        lote_2 = EntradaDetalle.objects.create(
            entrada=entrada, producto=producto, lote_proveedor="LOTE-2",
            camara=camara_origen, cajas=50, peso_por_caja=Decimal("10.00"),
            total_kilos=Decimal("500.00"),
        )

        salida = Salida.objects.create(folio_de_salida="SI9999", cliente=cliente, fecha="2026-01-27")
        salida_detalle_1 = SalidaDetalle.objects.create(
            salida=salida, producto=producto, entrada_detalle=lote_1,
            camara=camara_origen, cajas=100, total_kilos=Decimal("1000.00"),
        )
        salida_detalle_2 = SalidaDetalle.objects.create(
            salida=salida, producto=producto, entrada_detalle=lote_2,
            camara=camara_origen, cajas=50, total_kilos=Decimal("500.00"),
        )

        entrada_destino = Entrada.objects.create(fecha="2026-01-27", proveedor=proveedor)
        lote_1_destino = EntradaDetalle.objects.create(
            entrada=entrada_destino, producto=producto, lote_proveedor="LOTE-1",
            camara=camara_destino, cajas=100, peso_por_caja=Decimal("10.00"),
            total_kilos=Decimal("1000.00"),
        )
        lote_2_destino = EntradaDetalle.objects.create(
            entrada=entrada_destino, producto=producto, lote_proveedor="LOTE-2",
            camara=camara_destino, cajas=50, peso_por_caja=Decimal("10.00"),
            total_kilos=Decimal("500.00"),
        )

        movimiento_1 = MovimientoCamara.objects.create(
            entrada_detalle_origen=lote_1, salida_detalle=salida_detalle_1,
            entrada_detalle_destino=lote_1_destino, camara_origen=camara_origen,
            camara_destino=camara_destino, fecha="2026-01-27", cajas=100,
        )
        movimiento_2 = MovimientoCamara.objects.create(
            entrada_detalle_origen=lote_2, salida_detalle=salida_detalle_2,
            entrada_detalle_destino=lote_2_destino, camara_origen=camara_origen,
            camara_destino=camara_destino, fecha="2026-01-27", cajas=50,
        )

        self.assertEqual(salida.detalles.count(), 2)
        self.assertNotEqual(movimiento_1, movimiento_2)
        self.assertEqual(salida_detalle_1.movimiento_camara, movimiento_1)
        self.assertEqual(salida_detalle_2.movimiento_camara, movimiento_2)


class CamaraApiTests(APITestCase):
    def setUp(self):
        user = crear_usuario_con_area("tester", AREA_INVENTARIO)
        self.client.force_authenticate(user=user)

    def test_crear_y_listar_camaras(self):
        response = self.client.post(
            "/api/inventario/camaras/",
            {"nombre": "IMPORTADORA", "tipo": "propia"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)

        response = self.client.get("/api/inventario/camaras/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)


class EntradaApiTests(APITestCase):
    def setUp(self):
        user = crear_usuario_con_area("tester2", AREA_INVENTARIO)
        self.client.force_authenticate(user=user)
        self.proveedor = Proveedor.objects.create(nombre="CACESA")
        self.camara = Camara.objects.create(nombre="IMPORTADORA2", tipo=Camara.TIPO_PROPIA)
        self.producto = Producto.objects.create(talla="70-80", tipo="FREEZADO")

    def test_crear_entrada(self):
        response = self.client.post(
            "/api/inventario/entradas/",
            {
                "fecha": "2026-01-05",
                "proveedor_id": self.proveedor.id,
                "factura": "FACT 1070",
                "detalles": [
                    {
                        "producto_id": self.producto.id,
                        "lote_proveedor": "LOTE-TEST",
                        "camara": self.camara.id,
                        "cajas": 10,
                        "peso_por_caja": "18.00",
                        "total_kilos": "180.00",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)


class EntradaCreacionAnidadaApiTests(APITestCase):
    def setUp(self):
        self.user = crear_usuario_con_area("tester3", AREA_INVENTARIO)
        self.client.force_authenticate(user=self.user)
        self.proveedor = Proveedor.objects.create(nombre="CACESA")
        self.camara = Camara.objects.create(nombre="IMPORTADORA3", tipo=Camara.TIPO_PROPIA)
        self.producto = Producto.objects.create(talla="80-100", tipo="FREEZADO")

    def test_crear_entrada_con_dos_lineas_en_un_solo_post(self):
        payload = {
            "fecha": "2026-02-01",
            "proveedor_id": self.proveedor.id,
            "factura": "FACT 2001",
            "pedimento": "",
            "detalles": [
                {
                    "producto_id": self.producto.id,
                    "lote_proveedor": "LOTE-A",
                    "camara": self.camara.id,
                    "cajas": 10,
                    "peso_por_caja": "18.00",
                    "total_kilos": "180.00",
                },
                {
                    "producto_id": self.producto.id,
                    "lote_proveedor": "LOTE-B",
                    "camara": self.camara.id,
                    "cajas": 5,
                    "peso_por_caja": "18.00",
                    "total_kilos": "90.00",
                },
            ],
        }
        response = self.client.post("/api/inventario/entradas/", payload, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        entrada = Entrada.objects.get(id=response.data["id"])
        self.assertEqual(entrada.detalles.count(), 2)

    def test_crear_entrada_sin_lineas_falla(self):
        payload = {
            "fecha": "2026-02-01",
            "proveedor_id": self.proveedor.id,
            "factura": "",
            "pedimento": "",
            "detalles": [],
        }
        response = self.client.post("/api/inventario/entradas/", payload, format="json")

        self.assertEqual(response.status_code, 400)

    def test_entrada_internacional_sin_pedimento_falla(self):
        payload = {
            "fecha": "2026-02-01",
            "proveedor_id": self.proveedor.id,
            "es_internacional": True,
            "factura": "FACT 3001",
            "pedimento": "",
            "detalles": [
                {
                    "producto_id": self.producto.id,
                    "lote_proveedor": "LOTE-INTL",
                    "camara": self.camara.id,
                    "cajas": 10,
                    "peso_por_caja": "18.00",
                    "total_kilos": "180.00",
                },
            ],
        }
        response = self.client.post("/api/inventario/entradas/", payload, format="json")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("pedimento", response.data)

    def test_entrada_nacional_sin_pedimento_se_crea(self):
        payload = {
            "fecha": "2026-02-01",
            "proveedor_id": self.proveedor.id,
            "es_internacional": False,
            "factura": "FACT 3002",
            "pedimento": "",
            "detalles": [
                {
                    "producto_id": self.producto.id,
                    "lote_proveedor": "LOTE-NAL",
                    "camara": self.camara.id,
                    "cajas": 10,
                    "peso_por_caja": "18.00",
                    "total_kilos": "180.00",
                },
            ],
        }
        response = self.client.post("/api/inventario/entradas/", payload, format="json")

        self.assertEqual(response.status_code, 201, response.data)

    def test_entrada_sin_factura_falla(self):
        payload = {
            "fecha": "2026-02-01",
            "proveedor_id": self.proveedor.id,
            "factura": "",
            "detalles": [
                {
                    "producto_id": self.producto.id,
                    "lote_proveedor": "LOTE-SF",
                    "camara": self.camara.id,
                    "cajas": 10,
                    "peso_por_caja": "18.00",
                    "total_kilos": "180.00",
                },
            ],
        }
        response = self.client.post("/api/inventario/entradas/", payload, format="json")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("factura", response.data)

    def test_textos_libres_se_normalizan_a_mayusculas(self):
        payload = {
            "fecha": "2026-02-01",
            "proveedor_id": self.proveedor.id,
            "factura": "fact 3003",
            "detalles": [
                {
                    "producto_id": self.producto.id,
                    "lote_proveedor": "lote-minusculas",
                    "camara": self.camara.id,
                    "cajas": 10,
                    "peso_por_caja": "18.00",
                    "total_kilos": "180.00",
                    "observaciones": "revisar con calma",
                },
            ],
        }
        response = self.client.post("/api/inventario/entradas/", payload, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["factura"], "FACT 3003")
        self.assertEqual(response.data["detalles"][0]["lote_proveedor"], "LOTE-MINUSCULAS")
        self.assertEqual(response.data["detalles"][0]["observaciones"], "REVISAR CON CALMA")

    def test_las_lineas_heredan_el_proveedor_de_la_entrada_como_proveedor_origen(self):
        payload = {
            "fecha": "2026-02-01",
            "proveedor_id": self.proveedor.id,
            "factura": "FACT 3005",
            "detalles": [
                {
                    "producto_id": self.producto.id,
                    "lote_proveedor": "LOTE-D",
                    "camara": self.camara.id,
                    "cajas": 10,
                    "peso_por_caja": "18.00",
                    "total_kilos": "180.00",
                },
            ],
        }
        response = self.client.post("/api/inventario/entradas/", payload, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["detalles"][0]["proveedor_origen"]["id"], self.proveedor.id)

    def test_recibo_ingreso_registra_al_usuario_logueado_en_el_lote_general(self):
        payload = {
            "fecha": "2026-02-01",
            "proveedor_id": self.proveedor.id,
            "factura": "FACT 3004",
            "recibo_ingreso": "imp-9999",
            "detalles": [
                {
                    "producto_id": self.producto.id,
                    "lote_proveedor": "LOTE-C",
                    "camara": self.camara.id,
                    "cajas": 10,
                    "peso_por_caja": "18.00",
                    "total_kilos": "180.00",
                },
            ],
        }
        response = self.client.post("/api/inventario/entradas/", payload, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        lote_general = LoteGeneral.objects.get(codigo="IMP-9999")
        self.assertEqual(lote_general.creado_por_id, self.user.id)

    def test_peso_por_caja_en_cero_se_rechaza(self):
        # cajas_disponibles divide entre peso_por_caja: un 0 tumbaría existencias.
        payload = {
            "fecha": "2026-02-01",
            "proveedor_id": self.proveedor.id,
            "factura": "FACT 3006",
            "detalles": [
                {
                    "producto_id": self.producto.id,
                    "lote_proveedor": "LOTE-CERO",
                    "camara": self.camara.id,
                    "cajas": 10,
                    "peso_por_caja": "0.00",
                    "total_kilos": "180.00",
                },
            ],
        }
        response = self.client.post("/api/inventario/entradas/", payload, format="json")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("detalles", response.data)
        self.assertFalse(Entrada.objects.filter(factura="FACT 3006").exists())


class SalidaCreacionAnidadaApiTests(APITestCase):
    def setUp(self):
        user = crear_usuario_con_area("tester4", AREA_INVENTARIO)
        self.client.force_authenticate(user=user)
        self.proveedor = Proveedor.objects.create(nombre="CACESA")
        self.cliente = Cliente.objects.create(nombre="HERAY ACERO MORENO")
        self.camara = Camara.objects.create(nombre="IMPORTADORA4", tipo=Camara.TIPO_PROPIA)
        self.producto = Producto.objects.create(talla="90-110", tipo="FREEZADO")

        entrada = Entrada.objects.create(fecha="2026-02-01", proveedor=self.proveedor)
        self.lote_a = EntradaDetalle.objects.create(
            entrada=entrada, producto=self.producto, lote_proveedor="LOTE-A",
            camara=self.camara, cajas=60, peso_por_caja="18.00", total_kilos="1080.00",
        )
        self.lote_b = EntradaDetalle.objects.create(
            entrada=entrada, producto=self.producto, lote_proveedor="LOTE-B",
            camara=self.camara, cajas=40, peso_por_caja="18.00", total_kilos="720.00",
        )

    def test_crear_salida_con_lineas_de_lotes_distintos_en_un_solo_post(self):
        payload = {
            "folio_de_salida": "SI9500",
            "cliente_id": self.cliente.id,
            "fecha": "2026-02-05",
            "notas": "2999/25A",
            "detalles": [
                {
                    "producto_id": self.producto.id,
                    "entrada_detalle": self.lote_a.id,
                    "camara": self.camara.id,
                    "cajas": 60,
                    "total_kilos": "1080.00",
                    "factura_proveedor": "FACT 1070",
                    "precio_x_kilo": "140.00",
                },
                {
                    "producto_id": self.producto.id,
                    "entrada_detalle": self.lote_b.id,
                    "camara": self.camara.id,
                    "cajas": 40,
                    "total_kilos": "720.00",
                    "factura_proveedor": "FACT 1090",
                    "precio_x_kilo": "145.00",
                },
            ],
        }
        response = self.client.post("/api/inventario/salidas/", payload, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        salida = Salida.objects.get(id=response.data["id"])
        self.assertEqual(salida.detalles.count(), 2)

    def test_camara_de_la_linea_se_hereda_del_lote_e_ignora_lo_que_mande_el_cliente(self):
        # Una venta no puede "mover" mercancía a otra cámara — eso es exclusivo de
        # MovimientoCamara — así que aunque el payload mande una cámara distinta a
        # la real del lote, el backend debe ignorarla y usar la del lote de origen.
        otra_camara = Camara.objects.create(nombre="OTRA_CAMARA", tipo=Camara.TIPO_PROPIA)
        payload = {
            "folio_de_salida": "SI9600",
            "cliente_id": self.cliente.id,
            "fecha": "2026-02-06",
            "notas": "NS-9600",
            "detalles": [
                {
                    "producto_id": self.producto.id,
                    "entrada_detalle": self.lote_a.id,
                    "camara": otra_camara.id,
                    "cajas": 10,
                    "total_kilos": "180.00",
                },
            ],
        }
        response = self.client.post("/api/inventario/salidas/", payload, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["detalles"][0]["camara"], self.camara.id)
        salida_detalle = SalidaDetalle.objects.get(id=response.data["detalles"][0]["id"])
        self.assertEqual(salida_detalle.camara_id, self.camara.id)

    def test_salida_sin_nota_de_salida_es_rechazada(self):
        # La nota de salida es el folio del documento físico con el que sale la
        # mercancía: sin ella la salida del sistema no se amarra con nada.
        payload = {
            "folio_de_salida": "SI9601",
            "cliente_id": self.cliente.id,
            "fecha": "2026-02-06",
            "detalles": [
                {
                    "producto_id": self.producto.id,
                    "entrada_detalle": self.lote_a.id,
                    "cajas": 10,
                    "total_kilos": "180.00",
                },
            ],
        }
        response = self.client.post("/api/inventario/salidas/", payload, format="json")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("notas", response.data)
        self.assertFalse(Salida.objects.filter(folio_de_salida="SI9601").exists())

    def test_salida_con_cajas_en_cero_registra_kilos_sueltos(self):
        # lote_a entró con 60 cajas / 1080.00 kilos (18kg/caja). Vender kilos
        # sueltos de una caja ya abierta no debe mover ninguna caja completa.
        payload = {
            "folio_de_salida": "SI9700",
            "cliente_id": self.cliente.id,
            "fecha": "2026-02-07",
            "notas": "NS-9700",
            "detalles": [
                {
                    "producto_id": self.producto.id,
                    "entrada_detalle": self.lote_a.id,
                    "cajas": 0,
                    "total_kilos": "5.00",
                },
            ],
        }
        response = self.client.post("/api/inventario/salidas/", payload, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.lote_a.refresh_from_db()
        self.assertEqual(self.lote_a.kilos_disponibles, Decimal("1075.00"))
        # 1075.00 / 18.00 sigue dando piso 59: no se perdió ninguna caja completa.
        self.assertEqual(self.lote_a.cajas_disponibles, 59)

    def test_salida_que_excede_los_kilos_disponibles_del_lote_se_rechaza(self):
        # lote_b entró con 40 cajas / 720.00 kilos: pedir más de eso debe
        # rechazarse aunque las cajas pedidas sí alcancen (aquí ni se piden).
        payload = {
            "folio_de_salida": "SI9701",
            "cliente_id": self.cliente.id,
            "fecha": "2026-02-07",
            "notas": "NS-9701",
            "detalles": [
                {
                    "producto_id": self.producto.id,
                    "entrada_detalle": self.lote_b.id,
                    "cajas": 0,
                    "total_kilos": "800.00",
                },
            ],
        }
        response = self.client.post("/api/inventario/salidas/", payload, format="json")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("detalles", response.data)
        self.assertFalse(Salida.objects.filter(folio_de_salida="SI9701").exists())


class MovimientoCamaraApiTests(APITestCase):
    def setUp(self):
        user = crear_usuario_con_area("tester5", AREA_INVENTARIO)
        self.client.force_authenticate(user=user)
        self.proveedor = Proveedor.objects.create(nombre="ACUAMAYA")
        self.camara_origen = Camara.objects.create(nombre="FRIGARSA", tipo=Camara.TIPO_TERCERO)
        self.camara_destino = Camara.objects.create(nombre="MEXIDELI", tipo=Camara.TIPO_PROPIA)
        self.producto = Producto.objects.create(talla="41-50", tipo="FREEZADO")

        entrada = Entrada.objects.create(fecha="2026-02-01", proveedor=self.proveedor, factura="FACT 4001")
        self.lote_origen = EntradaDetalle.objects.create(
            entrada=entrada, producto=self.producto, lote_proveedor="LOTE-MOV",
            camara=self.camara_origen, cajas=100, peso_por_caja=Decimal("20.00"),
            total_kilos=Decimal("2000.00"),
            proveedor_origen=self.proveedor, fecha_caducidad="2026-06-01",
        )

    def test_el_lote_destino_hereda_el_proveedor_de_origen_no_del_entrada_de_llegada(self):
        # La "entrada de llegada" que arma el movimiento no tiene proveedor propio
        # (no es una compra real), pero el lote sigue siendo del mismo proveedor —
        # antes se perdía esta trazabilidad y las existencias mostraban "sin proveedor".
        payload = {
            "entrada_detalle_origen": self.lote_origen.id,
            "camara_destino": self.camara_destino.id,
            "fecha": "2026-02-10",
            "cajas": 40,
            "total_kilos": "800.00",
        }
        response = self.client.post("/api/inventario/movimientos-camara/", payload, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        lote_destino = EntradaDetalle.objects.get(id=response.data["entrada_detalle_destino"])
        self.assertEqual(lote_destino.proveedor_origen_id, self.proveedor.id)
        self.assertIsNone(lote_destino.entrada.proveedor_id)

    def test_el_lote_destino_hereda_la_fecha_de_caducidad_del_lote_de_origen(self):
        payload = {
            "entrada_detalle_origen": self.lote_origen.id,
            "camara_destino": self.camara_destino.id,
            "fecha": "2026-02-10",
            "cajas": 40,
            "total_kilos": "800.00",
        }
        response = self.client.post("/api/inventario/movimientos-camara/", payload, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        lote_destino = EntradaDetalle.objects.get(id=response.data["entrada_detalle_destino"])
        self.assertEqual(str(lote_destino.fecha_caducidad), "2026-06-01")


class ClasificarNivelTests(TestCase):
    def test_vencido_si_dias_restantes_es_negativo(self):
        self.assertEqual(clasificar_nivel(-1), "vencido")
        self.assertEqual(clasificar_nivel(-30), "vencido")

    def test_critico_hasta_7_dias(self):
        self.assertEqual(clasificar_nivel(0), "critico")
        self.assertEqual(clasificar_nivel(7), "critico")

    def test_urgente_de_8_a_15_dias(self):
        self.assertEqual(clasificar_nivel(8), "urgente")
        self.assertEqual(clasificar_nivel(15), "urgente")

    def test_por_vencer_de_16_a_30_dias(self):
        self.assertEqual(clasificar_nivel(16), "por_vencer")
        self.assertEqual(clasificar_nivel(30), "por_vencer")

    def test_ninguno_fuera_de_la_ventana_de_30_dias(self):
        self.assertIsNone(clasificar_nivel(31))
        self.assertIsNone(clasificar_nivel(365))


class AlertasCaducidadTests(TestCase):
    def setUp(self):
        self.proveedor = Proveedor.objects.create(nombre="ACUAMAYA")
        self.camara = Camara.objects.create(nombre="FRIGARSA", tipo=Camara.TIPO_TERCERO)
        self.producto = Producto.objects.create(talla="41-50", tipo="FREEZADO")
        self.hoy = timezone.localdate()

    def _crear_lote(self, cajas, dias_para_caducar=None, cajas_vendidas=0):
        entrada = Entrada.objects.create(fecha=self.hoy, proveedor=self.proveedor)
        fecha_caducidad = self.hoy + timedelta(days=dias_para_caducar) if dias_para_caducar is not None else None
        # peso_por_caja=10.00: consistente con total_kilos=100.00 solo porque todas
        # las llamadas de este helper usan cajas=10 — si eso cambia, hay que ajustar
        # total_kilos junto con cajas para no desalinear cajas_disponibles.
        lote = EntradaDetalle.objects.create(
            entrada=entrada, producto=self.producto, lote_proveedor="LOTE-X",
            camara=self.camara, cajas=cajas, peso_por_caja=Decimal("10.00"),
            total_kilos=Decimal("100.00"),
            proveedor_origen=self.proveedor, fecha_caducidad=fecha_caducidad,
        )
        if cajas_vendidas:
            cliente = Cliente.objects.create(nombre=f"CLIENTE-{lote.id}")
            salida = Salida.objects.create(folio_de_salida=f"SI-{lote.id}", cliente=cliente, fecha=self.hoy)
            SalidaDetalle.objects.create(
                salida=salida, producto=self.producto, entrada_detalle=lote,
                camara=self.camara, cajas=cajas_vendidas, total_kilos=Decimal(cajas_vendidas * 10),
            )
        return lote

    def test_excluye_lotes_sin_caducidad(self):
        self._crear_lote(cajas=10, dias_para_caducar=None)
        alertas = obtener_lotes_por_vencer({})
        self.assertEqual(len(alertas), 0)

    def test_excluye_lotes_fuera_de_la_ventana_de_30_dias(self):
        self._crear_lote(cajas=10, dias_para_caducar=45)
        alertas = obtener_lotes_por_vencer({})
        self.assertEqual(len(alertas), 0)

    def test_excluye_lotes_sin_cajas_disponibles(self):
        self._crear_lote(cajas=10, dias_para_caducar=5, cajas_vendidas=10)
        alertas = obtener_lotes_por_vencer({})
        self.assertEqual(len(alertas), 0)

    def test_incluye_y_clasifica_correctamente_por_nivel(self):
        self._crear_lote(cajas=10, dias_para_caducar=-2)
        self._crear_lote(cajas=10, dias_para_caducar=3)
        self._crear_lote(cajas=10, dias_para_caducar=10)
        self._crear_lote(cajas=10, dias_para_caducar=25)

        alertas = obtener_lotes_por_vencer({})

        self.assertEqual(len(alertas), 4)
        self.assertEqual([a['nivel'] for a in alertas], ["vencido", "critico", "urgente", "por_vencer"])

    def test_filtro_por_nivel(self):
        self._crear_lote(cajas=10, dias_para_caducar=3)
        self._crear_lote(cajas=10, dias_para_caducar=25)

        alertas = obtener_lotes_por_vencer({'nivel': 'critico'})

        self.assertEqual(len(alertas), 1)
        self.assertEqual(alertas[0]['nivel'], 'critico')


class AlertasCaducidadApiTests(APITestCase):
    def setUp(self):
        user = crear_usuario_con_area("tester6", AREA_INVENTARIO)
        self.client.force_authenticate(user=user)
        self.proveedor = Proveedor.objects.create(nombre="ACUAMAYA")
        self.camara = Camara.objects.create(nombre="FRIGARSA", tipo=Camara.TIPO_TERCERO)
        self.producto = Producto.objects.create(talla="41-50", tipo="FREEZADO")

    def test_endpoint_devuelve_totales_y_conteo_por_nivel(self):
        hoy = timezone.localdate()
        entrada = Entrada.objects.create(fecha=hoy, proveedor=self.proveedor)
        EntradaDetalle.objects.create(
            entrada=entrada, producto=self.producto, lote_proveedor="LOTE-Y",
            camara=self.camara, cajas=10, peso_por_caja=Decimal("10.00"),
            total_kilos=Decimal("100.00"),
            proveedor_origen=self.proveedor, fecha_caducidad=hoy + timedelta(days=3),
        )

        response = self.client.get("/api/inventario/alertas/caducidad/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["conteo_por_nivel"]["critico"], 1)
        self.assertEqual(response.data["alertas"][0]["nivel"], "critico")
