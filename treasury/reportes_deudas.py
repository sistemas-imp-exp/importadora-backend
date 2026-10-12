"""
Estado de cuenta de un deudor: cada deuda con su monto, abonado, saldo y el
historial de abonos (los ingresos de caja ligados a ella), y lo pendiente por
divisa. Recibe las deudas anotadas con Deuda.objects.con_saldo().
"""
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import Deuda
from .reportes import (
    COLOR_ENCABEZADO_RL,
    COLOR_FILA_CANCELADA,
    COLOR_FILA_CANCELADA_RL,
    ESTILOS,
    _celda_detalle,
    _estilizar_encabezado,
    _pie_pagina_pdf,
)

ESTADOS = {Deuda.ABIERTA: "Abierta", Deuda.PAGADA: "Pagada", Deuda.CANCELADA: "Cancelada"}


def _estado(deuda):
    return Deuda.estado_de(deuda.cancelada, deuda.saldo)


def _cantidad(abono):
    return sum((linea.cantidad for linea in abono.divisas.all()), Decimal("0"))


def _abonos(deuda):
    return sorted(deuda.abonos.all(), key=lambda m: (m.fecha, m.creado))


def pendiente_por_divisa(deudas):
    """{codigo: (simbolo, saldo)} de las deudas abiertas."""
    totales = {}
    for deuda in deudas:
        if _estado(deuda) != Deuda.ABIERTA:
            continue
        simbolo, total = totales.get(deuda.divisa.codigo, (deuda.divisa.simbolo, Decimal("0")))
        totales[deuda.divisa.codigo] = (simbolo, total + deuda.saldo)
    return dict(sorted(totales.items()))


def _dinero(simbolo, valor):
    return f"{simbolo}{valor:,.2f}"


def construir_pdf_estado_cuenta(deudor, deudas):
    buffer = BytesIO()
    documento = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.2 * cm,
        title=f"Estado de cuenta - {deudor.nombre}",
    )
    estilo_empresa = ParagraphStyle(
        "empresa", parent=ESTILOS["Heading1"], fontSize=15, textColor=COLOR_ENCABEZADO_RL, spaceAfter=1,
    )
    estilo_titulo = ParagraphStyle("titulo", parent=ESTILOS["Normal"], fontSize=12, spaceAfter=6)
    estilo_subtitulo = ParagraphStyle(
        "subtitulo", parent=ESTILOS["Normal"], fontSize=9.5, textColor=colors.HexColor("#444444"), spaceAfter=2,
    )
    estilo_generado = ParagraphStyle(
        "generado", parent=ESTILOS["Normal"], fontSize=8, textColor=colors.HexColor("#999999"), spaceBefore=4,
    )
    estilo_h2 = ParagraphStyle(
        "h2", parent=ESTILOS["Heading2"], fontSize=12, textColor=COLOR_ENCABEZADO_RL, spaceAfter=4,
    )
    estilo_deuda = ParagraphStyle("deuda", parent=ESTILOS["Normal"], fontSize=9.5, spaceAfter=3)
    estilo_nota = ParagraphStyle(
        "nota", parent=ESTILOS["Normal"], fontSize=8, textColor=colors.HexColor("#777777"), spaceAfter=2,
    )
    estilo_tabla = [
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_ENCABEZADO_RL),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]

    elementos = [
        Paragraph("Importadora y Exportadora de Mariscos", estilo_empresa),
        Paragraph("Estado de cuenta", estilo_titulo),
        Paragraph(f"<b>{escape(deudor.nombre)}</b> · {deudor.get_tipo_display()}"
                  + (f" · Tel. {escape(deudor.telefono)}" if deudor.telefono else ""), estilo_subtitulo),
        Paragraph(f"Generado el {timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M')}", estilo_generado),
        Spacer(1, 0.5 * cm),
        Paragraph("Saldo pendiente", estilo_h2),
    ]

    pendiente = pendiente_por_divisa(deudas)
    if pendiente:
        tabla = Table(
            [["Divisa", "Pendiente"]] + [[codigo, _dinero(simbolo, total)] for codigo, (simbolo, total) in pendiente.items()],
            colWidths=[2.5 * cm, 4 * cm],
        )
        tabla.setStyle(TableStyle(estilo_tabla + [("ALIGN", (1, 0), (1, -1), "RIGHT")]))
        elementos.append(tabla)
    else:
        elementos.append(Paragraph("Sin deudas abiertas.", ESTILOS["Normal"]))

    elementos += [Spacer(1, 0.5 * cm), Paragraph("Deudas", estilo_h2)]
    if not deudas:
        elementos.append(Paragraph("Sin deudas registradas.", ESTILOS["Normal"]))

    for deuda in deudas:
        simbolo = deuda.divisa.simbolo
        estado = _estado(deuda)
        bloque = [
            Paragraph(
                f"<b>{escape(deuda.concepto)}</b> · {deuda.fecha.strftime('%d/%m/%Y')} · {deuda.divisa.codigo} · "
                f"{ESTADOS[estado]}",
                estilo_deuda,
            ),
            Paragraph(
                f"Monto {_dinero(simbolo, deuda.monto)} · Abonado {_dinero(simbolo, deuda.abonado)} · "
                f"<b>Saldo {_dinero(simbolo, deuda.saldo)}</b>",
                estilo_deuda,
            ),
        ]
        if deuda.cancelada:
            bloque.append(Paragraph(f"Cancelada: {escape(deuda.motivo_cancelacion)}", estilo_nota))
        abonos = _abonos(deuda)
        if abonos:
            filas = [["Folio", "Fecha", "Concepto", "Abono", "Estado"]]
            for abono in abonos:
                cancelado = abono.cancelado
                filas.append([
                    _celda_detalle(abono.folio, TA_LEFT, cancelado),
                    _celda_detalle(abono.fecha.strftime("%d/%m/%Y"), TA_CENTER, cancelado),
                    _celda_detalle(abono.concepto, TA_LEFT, cancelado),
                    _celda_detalle(_dinero(simbolo, _cantidad(abono)), TA_RIGHT, cancelado),
                    _celda_detalle("Cancelado" if cancelado else "Activo", TA_CENTER, cancelado),
                ])
            tabla = Table(filas, colWidths=[2.2 * cm, 2.3 * cm, 7.6 * cm, 3 * cm, 2.2 * cm], repeatRows=1)
            estilo = list(estilo_tabla)
            for indice, abono in enumerate(abonos, start=1):
                if abono.cancelado:
                    estilo.append(("BACKGROUND", (0, indice), (-1, indice), COLOR_FILA_CANCELADA_RL))
            tabla.setStyle(TableStyle(estilo))
            bloque.append(tabla)
        else:
            bloque.append(Paragraph("Sin abonos.", estilo_nota))
        bloque.append(Spacer(1, 0.45 * cm))
        elementos.append(KeepTogether(bloque))

    documento.build(elementos, onFirstPage=_pie_pagina_pdf, onLaterPages=_pie_pagina_pdf)
    buffer.seek(0)
    return buffer


def construir_excel_estado_cuenta(deudor, deudas):
    wb = Workbook()
    fill_cancelado = PatternFill("solid", fgColor=COLOR_FILA_CANCELADA)
    font_cancelado = Font(strike=True, color="808080")

    ws = wb.active
    ws.title = "Deudas"
    encabezados = ["Fecha", "Concepto", "Divisa", "Monto", "Abonado", "Saldo", "Estado", "Notas"]
    ws.append(encabezados)
    _estilizar_encabezado(ws, len(encabezados))
    for deuda in deudas:
        ws.append([
            deuda.fecha, deuda.concepto, deuda.divisa.codigo,
            float(deuda.monto), float(deuda.abonado), float(deuda.saldo),
            ESTADOS[_estado(deuda)], deuda.motivo_cancelacion if deuda.cancelada else deuda.notas,
        ])
        fila = ws.max_row
        ws.cell(row=fila, column=1).number_format = "DD/MM/YYYY"
        for col in (4, 5, 6):
            ws.cell(row=fila, column=col).number_format = "#,##0.00"
        if deuda.cancelada:
            for col in range(1, len(encabezados) + 1):
                ws.cell(row=fila, column=col).font = font_cancelado
                ws.cell(row=fila, column=col).fill = fill_cancelado
    for col, ancho in zip("ABCDEFGH", (12, 36, 8, 14, 14, 14, 11, 36)):
        ws.column_dimensions[col].width = ancho
    ws.freeze_panes = "A2"

    ws_abonos = wb.create_sheet("Abonos")
    encabezados = ["Deuda", "Divisa", "Folio", "Fecha", "Concepto", "Abono", "Estado", "Motivo cancelación"]
    ws_abonos.append(encabezados)
    _estilizar_encabezado(ws_abonos, len(encabezados))
    for deuda in deudas:
        for abono in _abonos(deuda):
            ws_abonos.append([
                deuda.concepto, deuda.divisa.codigo, abono.folio, abono.fecha, abono.concepto,
                float(_cantidad(abono)), "Cancelado" if abono.cancelado else "Activo", abono.motivo_cancelacion,
            ])
            fila = ws_abonos.max_row
            ws_abonos.cell(row=fila, column=4).number_format = "DD/MM/YYYY"
            ws_abonos.cell(row=fila, column=6).number_format = "#,##0.00"
            if abono.cancelado:
                for col in range(1, len(encabezados) + 1):
                    ws_abonos.cell(row=fila, column=col).font = font_cancelado
                    ws_abonos.cell(row=fila, column=col).fill = fill_cancelado
    for col, ancho in zip("ABCDEFGH", (30, 8, 12, 12, 32, 14, 11, 30)):
        ws_abonos.column_dimensions[col].width = ancho
    ws_abonos.freeze_panes = "A2"

    ws_resumen = wb.create_sheet("Pendiente por divisa")
    ws_resumen.append(["Deudor", deudor.nombre])
    ws_resumen.append(["Tipo", deudor.get_tipo_display()])
    ws_resumen.append([])
    ws_resumen.append(["Divisa", "Pendiente"])
    for col in (1, 2):
        celda = ws_resumen.cell(row=4, column=col)
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="0E6B6F")
    for codigo, (_, total) in pendiente_por_divisa(deudas).items():
        ws_resumen.append([codigo, float(total)])
        ws_resumen.cell(row=ws_resumen.max_row, column=2).number_format = "#,##0.00"
    ws_resumen.column_dimensions["A"].width = 12
    ws_resumen.column_dimensions["B"].width = 30
    return wb
