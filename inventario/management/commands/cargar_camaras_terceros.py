"""
Carga las existencias de las cámaras de terceros desde el último corte del área
(docs/INVENTARIO CAMARAS FALTANTES.xlsm): una pestaña por cámara con el formato
de su kardex (FECHA, TALLA, TIPO, PESO MASTER, PROVEEDOR, recibo (LOTE, RD, RCP
o RECIBO), CAJAS, ENTRADA, SALIDA, saldo, FACTURA, CADUCIDAD, LOTE, COSTO…).

Se cargan los renglones con saldo, todo de la empresa IMPORTADORA: una Entrada
por recibo y fecha, un LoteGeneral por recibo y una línea por renglón con el
saldo como kilos. Las cajas salen de saldo / peso master (hacia abajo). Crea las
cámaras (rentadas de tercero), productos y proveedores que no existan.

Por decisión del área: la pestaña FABIAN DE LOS SANTOS es la cámara PUERTO;
los saldos negativos (una caja de más en la salida) quedan en 0; el renglón sin fecha toma la del
renglón de arriba; los renglones sin recibo quedan en el recibo <CÁMARA>-SR-<fecha>.

Modo prueba por defecto (valida todo y deshace); --aplicar guarda.
"""
import datetime
import re
from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from openpyxl import load_workbook

from inventario.models import Camara, Empresa, Entrada, EntradaDetalle, LoteGeneral, Producto, Proveedor

from .cargar_inventario_inicial import SIN_LOTE, Rollback, texto
from .cargar_inventario_inicial import Command as CargaInicial

EMPRESA = "IMPORTADORA"
# Pestaña del archivo -> cámara en el sistema.
CAMARAS = {
    "FRIGARSA": "FRIGARSA",
    "NAVECAP": "NAVECAP",
    "MEXIDELI": "MEXIDELI",
    "ARCOSA": "ARCOSA",
    "BASAL 4": "BASAL 4",
    "BASAL 7": "BASAL 7",
    "FABIAN DE LOS SANTOS": "PUERTO",
}
# Nombres del archivo -> nombre ya dado de alta en el sistema.
PROVEEDORES = {
    "MER SEAFOOD": "MER SEA FOOD",
    "BEIHAI QINGUO": "BEIHAI",
    "BORAL SAC": "BORAL",
    "PESCADERIA DEL PACIFICO": "PESCADERIA EL PACIFICO",
}
TRES = Decimal("0.001")
CUATRO = Decimal("0.0001")


def numero(valor):
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return Decimal(str(valor))


def peso_master(valor):
    """'20 KG', '9.07 KG' o 20 -> Decimal; None si no se entiende."""
    if numero(valor) is not None:
        return numero(valor)
    m = re.fullmatch(r"([\d.]+)\s*KGS?", texto(valor))
    return Decimal(m.group(1)) if m else None


def caducidad(valor):
    """(fecha o None, aviso). Fechas de Excel o textos como 'CADUCIDAD 19/12/2027', 'CAD 12.07.2027', '10.10.27'."""
    if isinstance(valor, datetime.datetime):
        return valor.date(), ""
    if isinstance(valor, datetime.date):
        return valor, ""
    t = texto(valor)
    if not t:
        return None, ""
    m = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})\b", t)
    if m:
        anio = int(m.group(3))
        try:
            return datetime.date(anio + 2000 if anio < 100 else anio, int(m.group(2)), int(m.group(1))), ""
        except ValueError:
            pass
    return None, f"Caducidad en el archivo: {t}"


def _columnas(encabezado):
    def col(*nombres, desde=0):
        return next((j for j, v in enumerate(encabezado) if v in nombres and j >= desde), None)

    c = {
        "fecha": col("FECHA"), "talla": col("TALLA"), "tipo": col("TIPO"), "peso": col("PESO MASTER"),
        "proveedor": col("PROVEEDOR"), "recibo": col("LOTE", "RD", "RCP", "RECIBO"), "salida": col("SALIDA"),
        "factura": col("FACTURA"), "caducidad": col("CADUCIDAD"), "costo": col("COSTO"),
    }
    faltan = [k for k, v in c.items() if v is None]
    if faltan:
        raise CommandError(f"Faltan las columnas {faltan} en el encabezado {encabezado}.")
    c["saldo"] = c["salida"] + 1  # SALDO, TOTAL o KILOS según la pestaña
    c["lote"] = col("LOTE", desde=c["caducidad"])  # lote del proveedor, después de CADUCIDAD
    return c


def leer_archivo(ruta):
    """(renglones, avisos) de las pestañas de CAMARAS con saldo distinto de 0."""
    libro = load_workbook(ruta, data_only=True, read_only=True)
    pestanas = {h.strip().upper(): h for h in libro.sheetnames}
    renglones, avisos = [], []
    for pestana, camara in CAMARAS.items():
        if pestana not in pestanas:
            raise CommandError(f"Falta la pestaña {pestana}.")
        filas = list(libro[pestanas[pestana]].iter_rows(values_only=True))
        n_enc = next((i for i, r in enumerate(filas[:10]) if "TALLA" in [texto(v) for v in r]), None)
        if n_enc is None:
            raise CommandError(f"{pestana}: no se encontró el encabezado.")
        c = _columnas([texto(v) for v in filas[n_enc]])
        fecha_anterior = None
        for n, r in enumerate(filas[n_enc + 1:], start=n_enc + 2):
            r = list(r) + [None] * 30
            if any(texto(v) == "TOTAL" for v in r[:8]):
                break
            fila = f"{pestana} fila {n}"
            fecha = r[c["fecha"]]
            if isinstance(fecha, datetime.datetime):
                fecha = fecha.date()
            saldo = numero(r[c["saldo"]])
            if not texto(r[c["talla"]]) or not saldo:
                fecha_anterior = fecha if isinstance(fecha, datetime.date) else fecha_anterior
                continue
            if saldo < 0:
                avisos.append(f"{fila}: saldo negativo ({saldo} kg), queda en 0.")
                continue
            notas = []
            if not isinstance(fecha, datetime.date):
                if fecha_anterior is None:
                    raise CommandError(f"{fila}: sin FECHA y sin renglón de arriba con fecha.")
                fecha = fecha_anterior
                notas.append("Fecha tomada del renglón de arriba")
                avisos.append(f"{fila}: sin fecha, toma la de arriba ({fecha:%d/%m/%Y}).")
            fecha_anterior = fecha
            peso = peso_master(r[c["peso"]])
            if not peso or peso <= 0:
                raise CommandError(f"{fila}: PESO MASTER no válido ({r[c['peso']]!r}).")
            fecha_cad, aviso_cad = caducidad(r[c["caducidad"]])
            costo = numero(r[c["costo"]])
            proveedor = texto(r[c["proveedor"]])
            l = {
                "fila": fila,
                "camara": camara,
                "fecha": fecha,
                "recibo": texto(r[c["recibo"]]) or f"{camara}-SR-{fecha:%Y%m%d}",
                "talla": texto(r[c["talla"]]),
                "tipo": texto(r[c["tipo"]]),
                "proveedor": PROVEEDORES.get(proveedor, proveedor),
                "peso": peso.quantize(TRES),
                "saldo": saldo.quantize(TRES),
                "factura": texto(r[c["factura"]]),
                "caducidad": fecha_cad,
                "lote_proveedor": (texto(r[c["lote"]]) if c["lote"] is not None else "") or SIN_LOTE,
                "costo": costo.quantize(CUATRO) if costo else None,
                "notas": notas + ([aviso_cad] if aviso_cad else []),
            }
            for campo in ("talla", "tipo", "proveedor"):
                if not l[campo]:
                    raise CommandError(f"{fila}: falta {campo}.")
            if not texto(r[c["recibo"]]):
                avisos.append(f"{fila}: sin recibo, queda en {l['recibo']}.")
            renglones.append(l)
    return renglones, avisos


class Command(BaseCommand):
    help = "Carga las existencias de las cámaras de terceros (modo prueba salvo --aplicar)."

    def add_arguments(self, parser):
        parser.add_argument("ruta", help="Ruta de INVENTARIO CAMARAS FALTANTES.xlsm.")
        parser.add_argument("--aplicar", action="store_true", help="Guarda la carga; sin esto solo la valida.")
        parser.add_argument("--usuario", help="Usuario que queda como creador (por defecto, el primer superusuario).")

    def handle(self, *args, **opciones):
        renglones, avisos = leer_archivo(opciones["ruta"])
        usuario = CargaInicial._usuario(self, opciones.get("usuario"))
        try:
            with transaction.atomic():
                resumen = self._cargar(renglones, usuario)
                if not opciones["aplicar"]:
                    raise Rollback
        except Rollback:
            self._imprimir(resumen, avisos)
            self.stdout.write(self.style.NOTICE("Modo prueba: se validó todo y no se guardó nada. Usa --aplicar para cargarlo."))
            return
        self._imprimir(resumen, avisos)
        self.stdout.write(self.style.SUCCESS("Existencias de cámaras de terceros cargadas."))

    def _cargar(self, renglones, usuario):
        try:
            empresa = Empresa.objects.get(nombre=EMPRESA)
        except Empresa.DoesNotExist:
            raise CommandError(f"No existe la empresa {EMPRESA}.")

        recibos = {l["recibo"] for l in renglones}
        existentes = sorted(LoteGeneral.objects.filter(codigo__in=recibos).values_list("codigo", flat=True))
        if existentes:
            raise CommandError(f"Ya existen {len(existentes)} recibos en el sistema (p. ej. {existentes[:5]}): ¿ya se cargó?")
        camaras_recibo = defaultdict(set)
        for l in renglones:
            camaras_recibo[l["recibo"]].add(l["camara"])
        repetidos = sorted(r for r, cs in camaras_recibo.items() if len(cs) > 1)
        if repetidos:
            raise CommandError(f"Recibos en más de una cámara: {repetidos[:5]}.")

        camaras, camaras_nuevas = {}, []
        for nombre in dict.fromkeys(CAMARAS.values()):
            camara = Camara.objects.filter(nombre=nombre).first()
            if camara is None:
                camara = Camara.objects.create(nombre=nombre, tipo=Camara.TIPO_TERCERO, empresa=empresa, creado_por=usuario)
                camaras_nuevas.append(nombre)
            elif camara.entradas_detalle.exists():
                raise CommandError(f"La cámara {nombre} ya tiene existencias: ¿ya se cargó?")
            camaras[nombre] = camara

        proveedores = {p.nombre.upper(): p for p in Proveedor.objects.all()}
        nuevos_proveedores = sorted({l["proveedor"] for l in renglones} - set(proveedores))
        for nombre in nuevos_proveedores:
            proveedores[nombre] = Proveedor.objects.create(nombre=nombre, creado_por=usuario)

        productos = {(p.talla.upper(), p.tipo.upper()): p for p in Producto.objects.all()}
        nuevos_productos = sorted({(l["talla"], l["tipo"]) for l in renglones} - set(productos))
        for talla, tipo in nuevos_productos:
            productos[(talla, tipo)] = Producto.objects.create(talla=talla, tipo=tipo, creado_por=usuario)

        lotes, entradas = {}, {}
        kilos = defaultdict(Decimal)
        for l in sorted(renglones, key=lambda l: (l["camara"], l["recibo"], l["fecha"])):
            camara = camaras[l["camara"]]
            if l["recibo"] not in lotes:
                lotes[l["recibo"]] = LoteGeneral.objects.create(
                    codigo=l["recibo"], camara=camara, fecha_recibo=l["fecha"], creado_por=usuario,
                )
            clave = (l["recibo"], l["fecha"])
            if clave not in entradas:
                entradas[clave] = Entrada.objects.create(fecha=l["fecha"], empresa=empresa, creado_por=usuario)
            detalle = EntradaDetalle(
                entrada=entradas[clave],
                producto=productos[(l["talla"], l["tipo"])],
                proveedor_origen=proveedores[l["proveedor"]],
                factura=l["factura"][:50],
                lote_general=lotes[l["recibo"]],
                lote_proveedor=l["lote_proveedor"][:50],
                camara=camara,
                cajas=int(l["saldo"] // l["peso"]),
                peso_por_caja=l["peso"],
                total_kilos=l["saldo"],
                costo_por_kilo=l["costo"],
                fecha_caducidad=l["caducidad"],
                observaciones=". ".join([f"Carga de cámaras de terceros ({l['fila']})"] + l["notas"]),
            )
            detalle.full_clean()
            detalle.save()
            kilos[l["camara"]] += l["saldo"]

        return {
            "entradas": len(entradas),
            "recibos": len(lotes),
            "lineas": len(renglones),
            "kilos": dict(kilos),
            "camaras_nuevas": camaras_nuevas,
            "proveedores_nuevos": nuevos_proveedores,
            "productos_nuevos": nuevos_productos,
            "sin_caducidad": sum(1 for l in renglones if l["caducidad"] is None),
            "sin_costo": sum(1 for l in renglones if l["costo"] is None),
            "sin_factura": sum(1 for l in renglones if not l["factura"]),
        }

    def _imprimir(self, r, avisos):
        self.stdout.write(f"Entradas: {r['entradas']} · recibos: {r['recibos']} · líneas: {r['lineas']}")
        for camara, kg in r["kilos"].items():
            self.stdout.write(f"  {camara}: {kg:,.3f} kg")
        self.stdout.write(f"  Total: {sum(r['kilos'].values()):,.3f} kg")
        self.stdout.write(f"Cámaras nuevas: {', '.join(r['camaras_nuevas']) or 'ninguna'}")
        self.stdout.write(f"Proveedores nuevos: {', '.join(r['proveedores_nuevos']) or 'ninguno'}")
        self.stdout.write(f"Productos nuevos: {', '.join(f'{t} {p}' for t, p in r['productos_nuevos']) or 'ninguno'}")
        self.stdout.write(f"Líneas sin caducidad: {r['sin_caducidad']} · sin costo: {r['sin_costo']} · sin factura: {r['sin_factura']}")
        for aviso in avisos:
            self.stdout.write(self.style.WARNING(aviso))
