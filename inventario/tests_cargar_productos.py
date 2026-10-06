"""Comando cargar_productos: modo prueba por defecto, crea y actualiza, nunca borra."""
import tempfile
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from openpyxl import Workbook

from .models import Producto


def _excel(filas):
    wb = Workbook()
    ws = wb.active
    ws.append(["TALLA ", "TIPO", "CATEGORIA", "PRESENTACION"])
    for fila in filas:
        ws.append(fila)
    archivo = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    wb.save(archivo.name)
    return archivo.name


class CargarProductosTests(TestCase):
    def setUp(self):
        Producto.objects.create(talla="50-60", tipo="FREEZADO", categoria="CAMARON", presentacion="entero")
        Producto.objects.create(talla="VIEJO", tipo="X")
        self.ruta = _excel([
            ("50-60 ", "FREEZADO", "CAMARON", "COLA"),       # existente: cambia presentación
            ("130-150", "FREEZADO", "CAMARON", "ENTERO"),
            ("130-150", "freezado ", "CAMARON", "ENTERO"),   # repetido tras limpiar
            ("SALMON ", "LONJA", "PESCADO", None),            # sin presentación
            (None, "RECTA", "PAPA", None),                    # incompleto
        ])

    def _correr(self, *extra):
        salida = StringIO()
        call_command("cargar_productos", self.ruta, *extra, stdout=salida)
        return salida.getvalue()

    def test_modo_prueba_no_guarda(self):
        salida = self._correr()
        self.assertIn("nuevos: 2", salida)
        self.assertIn("a actualizar: 1", salida)
        self.assertIn("repetido", salida)
        self.assertEqual(Producto.objects.count(), 2)

    def test_aplicar_crea_actualiza_y_conserva_lo_que_no_viene(self):
        self._correr("--aplicar")

        self.assertEqual(Producto.objects.get(talla="50-60").presentacion, "colas")
        self.assertEqual(Producto.objects.get(talla="130-150", tipo="FREEZADO").presentacion, "entero")
        self.assertEqual(Producto.objects.get(talla="SALMON").presentacion, "")
        self.assertTrue(Producto.objects.filter(talla="VIEJO").exists())
        self.assertEqual(Producto.objects.count(), 4)
        self.assertIn("nuevos: 0 · a actualizar: 0", self._correr())  # idempotente
