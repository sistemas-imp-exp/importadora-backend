from django.db import IntegrityError
from django.test import TestCase
from rest_framework.test import APITestCase

from .models import Camara, Cliente, Empresa, Proveedor, Producto


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
