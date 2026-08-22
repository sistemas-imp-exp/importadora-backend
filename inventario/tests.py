from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase
from rest_framework.test import APITestCase

from .models import Camara, Cliente, Empresa, Proveedor, Producto, Entrada, EntradaDetalle, LoteGeneral


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
