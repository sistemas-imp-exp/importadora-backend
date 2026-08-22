from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from rest_framework.test import APITestCase

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
                total_kilos=Decimal("100.00"),
            )


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
            total_kilos=Decimal("1080.00"),
        )
        self.lote_b = EntradaDetalle.objects.create(
            entrada=self.entrada,
            producto=self.producto,
            lote_proveedor="LOTE-B",
            camara=self.camara,
            cajas=40,
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
            camara=camara_origen, cajas=100, total_kilos=Decimal("1000.00"),
        )
        lote_2 = EntradaDetalle.objects.create(
            entrada=entrada, producto=producto, lote_proveedor="LOTE-2",
            camara=camara_origen, cajas=50, total_kilos=Decimal("500.00"),
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
            camara=camara_destino, cajas=100, total_kilos=Decimal("1000.00"),
        )
        lote_2_destino = EntradaDetalle.objects.create(
            entrada=entrada_destino, producto=producto, lote_proveedor="LOTE-2",
            camara=camara_destino, cajas=50, total_kilos=Decimal("500.00"),
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
        user = get_user_model().objects.create_user(username="tester", password="x")
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
        user = get_user_model().objects.create_user(username="tester2", password="x")
        self.client.force_authenticate(user=user)
        self.proveedor = Proveedor.objects.create(nombre="CACESA")

    def test_crear_entrada(self):
        response = self.client.post(
            "/api/inventario/entradas/",
            {"fecha": "2026-01-05", "proveedor_id": self.proveedor.id, "factura": "FACT 1070"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
