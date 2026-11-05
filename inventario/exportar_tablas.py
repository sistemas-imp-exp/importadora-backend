"""
Excel de las tablas de Entradas y Salidas: lo que se ve en pantalla con los
mismos filtros (búsqueda, fechas, empresa), sin paginar.

Entradas trae dos hojas: una fila por documento, con las columnas de la tabla,
y "Detalle" con una fila por línea (lo que en pantalla se despliega). Salidas,
plana como su tabla, trae una sola: una fila por línea con lo que se ve al buscar.
Las columnas siguen a EntradasTable.tsx / SalidasTable.tsx: cambiar ambos juntos.
"""
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font

from .formatos import FORMATO_KG, FORMATO_PRECIO_KG
from .reportes import _estilizar_encabezado

FORMATO_PESOS = '"$"#,##0.00'
FORMATO_FECHA = "DD/MM/YYYY"


def _numero(valor):
    return float(valor) if valor is not None else None


def _hoja(ws, titulo, encabezados, filas, formatos, anchos, total=None):
    """Llena una hoja: encabezado, filas, formatos por columna (1-based) y fila de total opcional."""
    ws.title = titulo
    ws.append(encabezados)
    _estilizar_encabezado(ws, len(encabezados))
    for fila in filas:
        ws.append(fila)
    if total and filas:
        ws.append(total)
        for celda in ws[ws.max_row]:
            celda.font = Font(bold=True)
    for col, formato in formatos.items():
        for (celda,) in ws.iter_rows(min_row=2, min_col=col, max_col=col):
            celda.number_format = formato
    for col, ancho in enumerate(anchos, start=1):
        ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = ancho
    ws.freeze_panes = "C2"


def libro_entradas(entradas):
    """`entradas`: queryset de EntradaViewSet (detalles con consumo anotado)."""
    resumen, detalle = [], []
    tot_cajas, tot_kilos = 0, Decimal("0")
    for e in entradas:
        lineas = list(e.detalles.all())
        cajas = sum(d.cajas for d in lineas)
        kilos = sum((d.total_kilos for d in lineas), Decimal("0"))
        caducidades = [d.fecha_caducidad for d in lineas if d.fecha_caducidad]
        con_salidas = any(d.total_kilos != d.kilos_disponibles for d in lineas)
        recibos = ", ".join(sorted({d.lote_general.codigo for d in lineas if d.lote_general_id}))
        # Una entrada puede traer varios proveedores, cada uno con sus documentos.
        proveedores = ", ".join(dict.fromkeys(d.proveedor_origen.nombre for d in lineas if d.proveedor_origen_id))
        facturas = ", ".join(dict.fromkeys(d.factura for d in lineas if d.factura))
        pedimentos = ", ".join(dict.fromkeys(d.pedimento for d in lineas if d.pedimento))
        empresa = e.empresa.nombre if e.empresa_id else "—"
        resumen.append([
            e.fecha, proveedores or "—", empresa, facturas or "—", pedimentos, recibos or "—",
            len(lineas), cajas, _numero(kilos), min(caducidades) if caducidades else None,
            "Importación" if e.es_internacional else "Nacional",
            "Sí" if getattr(e, "fue_editada", False) else "No",
            "Sí" if con_salidas else "No",
        ])
        tot_cajas += cajas
        tot_kilos += kilos
        for d in lineas:
            detalle.append([
                e.fecha, d.proveedor_origen.nombre if d.proveedor_origen_id else "—", empresa,
                d.factura or "—", d.pedimento or "",
                d.lote_general.codigo if d.lote_general_id else "—",
                f"{d.producto.talla} {d.producto.tipo}", d.lote_proveedor,
                d.camara.nombre if d.camara_id else "Venta directa",
                d.cajas, _numero(d.peso_por_caja), _numero(d.total_kilos), _numero(d.kilos_disponibles),
                _numero(d.costo_por_kilo), d.fecha_caducidad, d.observaciones or "",
            ])

    wb = Workbook()
    _hoja(
        wb.active, "Entradas",
        ["Fecha", "Proveedor", "Empresa", "Factura", "Pedimento", "Recibo ingreso", "Líneas", "Cajas",
         "Total kg", "Caducidad próxima", "Tipo", "Editada", "Con salidas"],
        resumen,
        {1: FORMATO_FECHA, 8: "#,##0", 9: FORMATO_KG, 10: FORMATO_FECHA},
        [12, 24, 20, 16, 16, 18, 8, 10, 13, 16, 13, 9, 11],
        total=[None, f"Total ({len(resumen)} entradas)", None, None, None, None, None,
               tot_cajas, _numero(tot_kilos), None, None, None, None],
    )
    _hoja(
        wb.create_sheet(), "Detalle",
        ["Fecha", "Proveedor", "Empresa", "Factura", "Pedimento", "Recibo", "Producto", "Lote proveedor", "Cámara",
         "Cajas", "Kg/caja", "Total kg", "Disponible kg", "Costo/kg", "Caducidad", "Observaciones"],
        detalle,
        {1: FORMATO_FECHA, 10: "#,##0", 11: FORMATO_KG, 12: FORMATO_KG, 13: FORMATO_KG,
         14: FORMATO_PRECIO_KG, 15: FORMATO_FECHA},
        [12, 24, 20, 16, 16, 14, 20, 16, 20, 9, 10, 12, 13, 12, 12, 30],
    )
    return wb


def lineas_visibles(salida, busqueda):
    """
    Líneas de la salida que se ven con la búsqueda (igual que SalidasTable.tsx):
    todas si coincide con el folio, cliente o nota; si no, solo las que coinciden
    por recibo, talla o tipo (o todas si ninguna, p. ej. recibo de origen de un traslado).
    """
    lineas = list(salida.detalles.all())
    q = (busqueda or "").strip().lower()
    if not q:
        return lineas

    def contiene(texto):
        return q in (texto or "").lower()

    if contiene(salida.folio_de_salida) or contiene(salida.cliente.nombre if salida.cliente_id else "") or contiene(salida.notas):
        return lineas
    coinciden = [
        d for d in lineas
        if contiene(d.producto.talla) or contiene(d.producto.tipo)
        or (d.entrada_detalle_id and contiene(d.entrada_detalle.documento_origen["recibo"]))
    ]
    return coinciden or lineas


def libro_salidas(salidas, busqueda=""):
    """`salidas`: queryset de SalidaViewSet. Una fila por línea, como la tabla (sin hoja de detalle)."""
    filas = []
    tot_cajas, tot_kilos, tot_venta = 0, Decimal("0"), Decimal("0")
    for s in salidas:
        cliente = s.cliente.nombre if s.cliente_id else "—"
        for d in lineas_visibles(s, busqueda):
            origen = d.entrada_detalle
            notas = " · ".join(x for x in (s.notas, d.notas) if x)
            filas.append([
                s.fecha, d.producto.talla, d.producto.tipo,
                _numero(origen.peso_por_caja) if origen else None,
                origen.proveedor_origen.nombre if origen and origen.proveedor_origen_id else "—",
                d.camara.nombre if d.camara_id else "—",
                # Recibo de la cámara de donde salió: el de destino si el lote llegó por traslado.
                (origen.documento_origen["recibo"] or "—") if origen else "—",
                (origen.lote_proveedor or "—") if origen else "—",
                s.folio_de_salida or "Sin salida (resguardo)", cliente, notas,
                d.cajas, _numero(d.total_kilos), _numero(d.precio_x_kilo),
                None if s.folio_retiro else _numero(d.total_venta),
                d.factura_proveedor or "",
            ])
            tot_cajas += d.cajas
            tot_kilos += d.total_kilos
            tot_venta += Decimal("0") if s.folio_retiro else (d.total_venta or Decimal("0"))

    wb = Workbook()
    _hoja(
        wb.active, "Salidas",
        ["Fecha", "Talla", "Tipo", "Peso por caja", "Proveedor", "Cámara", "Recibo (lote)", "Lote proveedor",
         "Folio de salida", "Cliente", "Notas", "Cajas", "Total kg", "Precio x kilo", "Total venta", "Factura"],
        filas,
        {1: FORMATO_FECHA, 4: FORMATO_KG, 12: "#,##0", 13: FORMATO_KG, 14: FORMATO_PRECIO_KG, 15: FORMATO_PESOS},
        [12, 14, 14, 11, 26, 16, 14, 18, 14, 28, 30, 9, 13, 12, 15, 14],
        total=[None, f"Total ({len(filas)} líneas)"] + [None] * 9
        + [tot_cajas, _numero(tot_kilos), None, _numero(tot_venta), None],
    )
    return wb
