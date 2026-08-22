from django.test import TestCase
from rest_framework.test import APITestCase

from .models import Camara, Cliente, Empresa, Proveedor


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
