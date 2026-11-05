"""Comando cargar_camaras_terceros: existencias de las cámaras de terceros desde el corte del área."""
import datetime
import tempfile
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from openpyxl import Workbook

from .management.commands.cargar_camaras_terceros import CAMARAS
from .models import Camara, Empresa, Entrada, EntradaDetalle, LoteGeneral, Producto, Proveedor

ENCABEZADOS = ["FECHA", "TALLA", "TIPO", "PESO MASTER", "PROVEEDOR", "RCP", "CAJAS", "ENTRADA", "SALIDA", "TOTAL",
               "FACTURA ", "CADUCIDAD", "LOTE", "COSTO"]
F = datetime.datetime(2025, 10, 23)


def _fila(recibo, talla, entrada, salida=0, fecha=F, proveedor="BEE GEE", caducidad=None, costo=95):
    return [fecha, talla, "MARQUETA", "20 KG", proveedor, recibo, None, entrada, salida, entrada - salida,
            "FACT 61", caducidad, None, costo]


def _excel(hojas):
    wb = Workbook()
    wb.remove(wb.active)
    for pestana in CAMARAS:
        ws = wb.create_sheet(pestana + " ")  # el archivo trae espacios de sobra en algunos nombres
        ws.append([None, None, f"INVENTARIO {pestana}"])
        ws.append(ENCABEZADOS)
        for fila in hojas.get(pestana, []):
            ws.append(fila)
        ws.append([None] * 6 + ["TOTAL", 0])
        ws.append(_fila("NO-SE-LEE", "41-50", 100))  # lo de abajo del total no se carga
    archivo = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    wb.save(archivo.name)
    return archivo.name


class CargarCamarasTercerosTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser("admin", password="x")
        Empresa.objects.get_or_create(nombre="IMPORTADORA")
        Camara.objects.create(nombre="MEXIDELI", tipo=Camara.TIPO_TERCERO)
        Producto.objects.create(talla="41-50", tipo="MARQUETA")
        Proveedor.objects.create(nombre="MER SEA FOOD")
        Proveedor.objects.create(nombre="BEE GEE")
        self.ruta = _excel({
            "ARCOSA": [
                _fila("RD1", "41-50", 1700, caducidad="CADUCIDAD 19/12/2027"),
                _fila("RD1", "51-60", 2210, proveedor="MER SEAFOOD", caducidad="10.10.27"),
                _fila("RD2", "41-50", 4300, 4300),  # agotado
                _fila("RD3", "41-50", 4300, 4320),  # negativo
            ],
            "MEXIDELI": [
                _fila("I-1", "41-50", 900, costo=None),
                _fila("I-1", "41-50", 500, fecha=None),
                _fila("I-1", "41-50", 1000, fecha=datetime.datetime(2025, 10, 28)),
            ],
            "FABIAN DE LOS SANTOS": [_fila(None, "41-50", 640)],
        })

    def _correr(self, *extra):
        salida = StringIO()
        call_command("cargar_camaras_terceros", self.ruta, *extra, stdout=salida)
        return salida.getvalue()

    def test_modo_prueba_no_guarda(self):
        salida = self._correr()
        self.assertIn("Total: 6,950.000 kg", salida)
        self.assertIn("Modo prueba", salida)
        self.assertEqual((Entrada.objects.count(), Camara.objects.count()), (0, 1))

    def test_aplica_por_recibo_con_saldo(self):
        salida = self._correr("--aplicar")
        self.assertIn("ARCOSA fila 6: saldo negativo (-20 kg), queda en 0.", salida)
        self.assertIn("MEXIDELI fila 4: sin fecha, toma la de arriba (23/10/2025).", salida)
        self.assertIn("Proveedores nuevos: ninguno", salida)  # MER SEAFOOD = MER SEA FOOD

        puerto = Camara.objects.get(nombre="PUERTO")
        self.assertEqual((puerto.tipo, puerto.empresa.nombre), (Camara.TIPO_TERCERO, "IMPORTADORA"))
        self.assertEqual(LoteGeneral.objects.get(codigo="PUERTO-SR-20251023").camara, puerto)

        linea = EntradaDetalle.objects.get(lote_general__codigo="RD1", producto__talla="51-60")
        self.assertEqual(linea.fecha_caducidad, datetime.date(2027, 10, 10))
        self.assertEqual((linea.cajas, linea.total_kilos), (110, Decimal("2210.000")))
        self.assertEqual(linea.proveedor_origen.nombre, "MER SEA FOOD")
        self.assertFalse(LoteGeneral.objects.filter(codigo__in=["RD2", "RD3", "NO-SE-LEE"]).exists())

        # Un recibo con dos fechas: un lote, dos entradas.
        i1 = LoteGeneral.objects.get(codigo="I-1")
        self.assertEqual(i1.entradas_detalle.values("entrada").distinct().count(), 2)
        self.assertEqual(i1.entradas_detalle.filter(costo_por_kilo__isnull=True).count(), 1)
        self.assertTrue(Entrada.objects.filter(empresa__nombre="IMPORTADORA").exists())

    def test_no_carga_dos_veces(self):
        self._correr("--aplicar")
        with self.assertRaisesMessage(CommandError, "¿ya se cargó?"):
            self._correr("--aplicar")
