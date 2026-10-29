"""Comando cargar_inventario_inicial: una entrada por recibo con el saldo del CONSOLIDADO; modo prueba por defecto."""
import datetime
import tempfile
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from openpyxl import Workbook

from .models import Camara, Empresa, Entrada, EntradaDetalle, LoteGeneral, Producto, Proveedor

ENCABEZADOS = [
    "FECHA", "TALLA", "TIPO", "PESO MASTER (KG)", "PROVEEDOR", "LOTE", "ENTREGADO A/ GUARDADO EN",
    "ENTRADA", "SALIDA", "SALDO", "OBSERVACIONES", "CADUCIDAD", "LOTE PROVEEDOR", "FACTURA", "COSTO/KG", "NOTAS",
]
F = datetime.datetime(2025, 3, 2)


def _excel(importadora, selectos=()):
    wb = Workbook()
    wb.remove(wb.active)
    for nombre, filas in (("IMPORTADORA", importadora), ("SELECTOS", selectos)):
        ws = wb.create_sheet(nombre)
        ws.append([f"CONTROL DE INVENTARIO {nombre}"])
        ws.append(ENCABEZADOS)
        for fila in filas:
            ws.append(fila)
        ws.append([None] * 8 + ["TOTAL", 0])  # renglón de total como en el CONSOLIDADO
    archivo = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    wb.save(archivo.name)
    return archivo.name


def _fila(recibo, talla, saldo, proveedor="BEE GEE", peso=20, factura="1185", lote="L-1", caducidad=None, costo=96.73):
    return [F, talla, "FREEZADO", peso, proveedor, recibo, "IMPORTADORA", saldo, 0, saldo,
            "LOTE: X", caducidad, lote, factura, costo, None]


class CargarInventarioInicialTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser("admin", password="x")
        Empresa.objects.get_or_create(nombre="IMPORTADORA")
        Empresa.objects.get_or_create(nombre="MARISCOS SELECTOS")
        Camara.objects.create(nombre="IMPORTADORA", tipo=Camara.TIPO_PROPIA)
        Producto.objects.create(talla="41-50", tipo="FREEZADO")
        self.ruta = _excel(
            [
                _fila("IMP25", "41-50", 1000),
                _fila("IMP25", "51-60", 45.5, proveedor="CAMANICA", factura="3177", caducidad="31/04/2025"),
                _fila("IMP30", "41-50", 30, caducidad=datetime.datetime(2027, 1, 5), lote=None),
            ],
            [_fila("MS05", "41-50", 18, peso=18, caducidad="N/A")],
        )

    def _correr(self, *extra):
        salida = StringIO()
        call_command("cargar_inventario_inicial", self.ruta, *extra, stdout=salida)
        return salida.getvalue()

    def test_modo_prueba_valida_y_no_guarda(self):
        salida = self._correr()
        self.assertIn("Recibos (entradas): 3 · líneas: 4", salida)
        self.assertIn("Total: 1,093.50 kg", salida)
        self.assertIn("Modo prueba", salida)
        self.assertEqual((Entrada.objects.count(), Producto.objects.count(), Proveedor.objects.count()), (0, 1, 0))

    def test_aplica_una_entrada_por_recibo_con_el_saldo(self):
        salida = self._correr("--aplicar")
        self.assertIn("productos nuevos: 1", salida)
        self.assertIn("IMPORTADORA fila 4: Caducidad en el archivo: 31/04/2025", salida)

        imp25 = LoteGeneral.objects.get(codigo="IMP25").entradas_detalle.first().entrada
        self.assertEqual((imp25.fecha, imp25.empresa.nombre), (F.date(), "IMPORTADORA"))
        # Cada línea con su proveedor y su factura.
        self.assertEqual(
            sorted(imp25.detalles.values_list("proveedor_origen__nombre", "factura")),
            [("BEE GEE", "1185"), ("CAMANICA", "3177")],
        )
        linea = imp25.detalles.get(producto__talla="51-60")
        self.assertEqual(linea.proveedor_origen.nombre, "CAMANICA")
        self.assertEqual((linea.total_kilos, linea.cajas, linea.peso_por_caja), (Decimal("45.50"), 2, Decimal("20")))
        self.assertIsNone(linea.fecha_caducidad)
        self.assertEqual(linea.factura, "3177")

        imp30 = EntradaDetalle.objects.get(lote_general__codigo="IMP30")
        self.assertEqual((imp30.lote_proveedor, imp30.fecha_caducidad), ("SIN LOTE", datetime.date(2027, 1, 5)))
        self.assertEqual(EntradaDetalle.objects.get(lote_general__codigo="MS05").entrada.empresa.nombre, "MARISCOS SELECTOS")
        self.assertEqual(sum(l.kilos_disponibles for l in EntradaDetalle.objects.con_consumo()), Decimal("1093.50"))

    def test_no_carga_dos_veces(self):
        self._correr("--aplicar")
        with self.assertRaisesMessage(CommandError, "Ya existen 3 recibos"):
            self._correr("--aplicar")

    def test_borrar_movimientos_quita_las_capturas_de_prueba(self):
        self._correr("--aplicar")
        salida = self._correr("--aplicar", "--borrar-movimientos")
        self.assertIn("Borrado previo (capturas de prueba): 3 entradas", salida)
        self.assertEqual(Entrada.objects.count(), 3)
