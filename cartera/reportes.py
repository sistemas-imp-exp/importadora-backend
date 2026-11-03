"""PDF y Excel de Cartera: estado de cuenta por cliente y saldos de clientes."""
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from inventario.formatos import FORMATO_KG, FORMATO_PRECIO_KG, kg, precio_kg
from .antiguedad import partes_aplicadas, texto_aplicacion
from treasury.reportes import (
    COLOR_ENCABEZADO,
    COLOR_ENCABEZADO_RL,
    COLOR_FILA_CANCELADA,
    COLOR_FILA_CANCELADA_RL,
    ESTILOS,
    _celda_detalle,
    _estilizar_encabezado,
    _pie_pagina_pdf,
)

FORMATO_DINERO = '#,##0.00;[Red]-#,##0.00'


def _dinero(valor):
    return '' if valor is None else f"${valor:,.2f}"


def _generado():
    return f"Generado el {timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M')}"


def _estilos():
    return {
        'empresa': ParagraphStyle('empresa', parent=ESTILOS['Heading1'], fontSize=15,
                                  textColor=COLOR_ENCABEZADO_RL, spaceAfter=1),
        'titulo': ParagraphStyle('titulo', parent=ESTILOS['Normal'], fontSize=12, spaceAfter=4),
        'subtitulo': ParagraphStyle('subtitulo', parent=ESTILOS['Normal'], fontSize=9.5,
                                    textColor=colors.HexColor('#444444'), spaceAfter=2),
        'generado': ParagraphStyle('generado', parent=ESTILOS['Normal'], fontSize=8,
                                   textColor=colors.HexColor('#999999'), spaceBefore=2),
    }


def _estilo_tabla(extra=()):
    return TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), COLOR_ENCABEZADO_RL),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#DDDDDD')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        *extra,
    ])


# Ancho útil de la hoja con los márgenes de _documento (carta: 21.59 x 27.94 cm).
ANCHO_UTIL_CM = {False: 21.59 - 2.4, True: 27.94 - 2.4}


def _anchos(proporciones, horizontal=False):
    """Anchos de columna en proporción, escalados para que la tabla quepa en la hoja."""
    total = sum(proporciones)
    return [a * ANCHO_UTIL_CM[horizontal] / total * cm for a in proporciones]


def datos_cliente(cliente, perfil, empresa=None):
    """(etiqueta, valor) del encabezado del estado de cuenta; los vacíos van como guion."""
    datos = [
        ('Cliente', cliente.nombre),
        ('Domicilio', perfil.domicilio if perfil else ''),
        ('Teléfono', perfil.celular if perfil else ''),
        ('RFC', perfil.rfc if perfil else ''),
    ]
    if empresa is not None:
        datos.append(('Empresa', empresa.nombre))
    if perfil and perfil.pagare:
        datos.append(('Pagaré', 'Firmado'))
    return [(etiqueta, valor or '—') for etiqueta, valor in datos]


def _documento(buffer, titulo, horizontal=False):
    return SimpleDocTemplate(
        buffer, pagesize=landscape(letter) if horizontal else letter, title=titulo,
        leftMargin=1.2 * cm, rightMargin=1.2 * cm, topMargin=1.2 * cm, bottomMargin=1.2 * cm,
    )


# --- Estado de cuenta -------------------------------------------------------

COLUMNAS_ESTADO = ['Nota', 'Folio salida', 'Concepto', 'Fecha', 'Talla', 'Kilos', 'Precio', 'Parcial',
                   'Venta neta', 'Depósito', 'Saldo']


def filas_estado_cuenta(renglones):
    """
    Filas como el estado de cuenta del área: una por producto vendido (con su
    parcial) y la venta neta en la última de la salida; pagos y abonos en
    Depósito; el saldo corrido en el renglón que lo mueve. Cada fila:
    (valores, cancelado).
    """
    filas = []
    for r in renglones:
        if r.tipo == 'venta' and r.detalle:
            for i, d in enumerate(r.detalle):
                ultima = i == len(r.detalle) - 1
                filas.append(([
                    d['nota'], d['folio_salida'], d['origen'], r.fecha, d['producto'], d['kilos'], d['precio_kilo'],
                    d['importe'], r.cargo if ultima else None, None, r.saldo if ultima else None,
                ], r.cancelado))
            continue
        concepto = r.concepto + (f' — Cancelado: {r.motivo_cancelacion}' if r.cancelado else '')
        if r.referencia and r.tipo != 'venta':
            concepto += f' ({r.referencia})'
        filas.append(([
            '', '', concepto, r.fecha, '', None, None, None,
            r.cargo if r.cargo else None, r.abono if r.abono else None, r.saldo,
        ], r.cancelado))
    return filas


def construir_pdf_estado_cuenta(cliente, perfil, secciones, conjunto=None):
    """
    `secciones`: [(empresa, renglones, saldo)], una por empresa; cada una
    empieza en hoja nueva con el encabezado del cliente, para poder entregarla
    por separado. `conjunto`: saldo de todas las empresas (si son varias).
    """
    buffer = BytesIO()
    documento = _documento(buffer, f'Estado de cuenta - {cliente.nombre}', horizontal=True)
    estilos = _estilos()
    etiqueta = ParagraphStyle('dato_etiqueta', parent=ESTILOS['Normal'], fontSize=8.5, fontName='Helvetica-Bold',
                              textColor=colors.HexColor('#555555'))
    valor = ParagraphStyle('dato_valor', parent=ESTILOS['Normal'], fontSize=9)
    elementos = []
    for indice, (empresa, renglones, saldo) in enumerate(secciones):
        if indice:
            elementos.append(PageBreak())
        datos = Table(
            [[Paragraph(escape(e), etiqueta), Paragraph(escape(v), valor)] for e, v in datos_cliente(cliente, perfil, empresa)],
            colWidths=[2.4 * cm, 13 * cm], hAlign='LEFT',
        )
        datos.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 1), ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
            ('LEFTPADDING', (0, 0), (0, -1), 0),
        ]))
        resumen = f"Saldo actual: <b>{_dinero(saldo)}</b>"
        if conjunto is not None:
            resumen += f" · Saldo conjunto (todas las empresas): <b>{_dinero(conjunto)}</b>"
        elementos += [
            Paragraph('Importadora y Exportadora de Mariscos de Centro América y el Caribe', estilos['empresa']),
            Paragraph('Estado de cuenta', estilos['titulo']),
            datos,
            Spacer(1, 0.15 * cm),
            Paragraph(resumen, estilos['subtitulo']),
            Paragraph(_generado(), estilos['generado']),
            Spacer(1, 0.4 * cm),
            _tabla_estado_cuenta(renglones),
        ]
    documento.build(elementos, onFirstPage=_pie_pagina_pdf, onLaterPages=_pie_pagina_pdf)
    buffer.seek(0)
    return buffer


def _tabla_estado_cuenta(renglones):
    filas = [COLUMNAS_ESTADO]
    extra = []
    for valores, c in filas_estado_cuenta(renglones):
        nota, folio, concepto, fecha, talla, kilos, precio, parcial, venta, deposito, saldo = valores
        filas.append([
            _celda_detalle(nota, TA_LEFT, c), _celda_detalle(folio, TA_LEFT, c), _celda_detalle(concepto, TA_LEFT, c),
            _celda_detalle(fecha.strftime('%d/%m/%Y'), TA_CENTER, c), _celda_detalle(talla, TA_LEFT, c),
            _celda_detalle(kg(kilos) if kilos is not None else '', TA_RIGHT, c),
            _celda_detalle(precio_kg(precio) if precio is not None else '', TA_RIGHT, c),
            _celda_detalle(_dinero(parcial) if parcial is not None else ('Sin precio' if talla else ''), TA_RIGHT, c),
            _celda_detalle(_dinero(venta) if venta is not None else '', TA_RIGHT, c),
            _celda_detalle(_dinero(deposito) if deposito is not None else '', TA_RIGHT, c),
            _celda_detalle(_dinero(saldo) if saldo is not None else '', TA_RIGHT, False),
        ])
        if c:
            extra.append(('BACKGROUND', (0, len(filas) - 1), (-1, len(filas) - 1), COLOR_FILA_CANCELADA_RL))
    if len(filas) == 1:
        filas.append(['', '', 'Sin movimientos.', '', '', '', '', '', '', '', ''])
    anchos = [1.9, 1.9, 4.6, 1.9, 2.9, 1.7, 1.8, 2.3, 2.4, 2.4, 2.6]
    tabla = Table(filas, colWidths=_anchos(anchos, horizontal=True), repeatRows=1)
    tabla.setStyle(_estilo_tabla(extra))
    return tabla


def construir_excel_estado_cuenta(cliente, perfil, secciones):
    """Una hoja por empresa (`secciones`: [(empresa, renglones, saldo)]), con el encabezado del cliente."""
    wb = Workbook()
    wb.remove(wb.active)
    for empresa, renglones, _ in secciones:
        nombre = empresa.nombre if empresa is not None else 'Estado de cuenta'
        _hoja_estado_cuenta(wb.create_sheet(nombre[:31]), cliente, perfil, empresa, renglones)
    return wb


def _hoja_estado_cuenta(ws, cliente, perfil, empresa, renglones):
    # Encabezado con los datos del cliente y, debajo, la tabla.
    ws.append(['Estado de cuenta'])
    ws.cell(row=1, column=1).font = Font(bold=True, size=13)
    for etiqueta, valor in datos_cliente(cliente, perfil, empresa):
        ws.append([etiqueta, valor])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        # Capturado por usuarios: siempre texto, nunca fórmula.
        ws.cell(row=ws.max_row, column=2).data_type = 's'
    ws.append([_generado()])
    ws.cell(row=ws.max_row, column=1).font = Font(italic=True, color='808080')
    ws.append([])
    ws.append(COLUMNAS_ESTADO)
    encabezado = ws.max_row
    for col in range(1, len(COLUMNAS_ESTADO) + 1):
        celda = ws.cell(row=encabezado, column=col)
        celda.font = Font(bold=True, color='FFFFFF')
        celda.fill = PatternFill('solid', fgColor=COLOR_ENCABEZADO)
    gris = PatternFill('solid', fgColor=COLOR_FILA_CANCELADA)
    tachado = Font(strike=True, color='808080')
    for valores, cancelado in filas_estado_cuenta(renglones):
        ws.append([float(v) if isinstance(v, Decimal) else v for v in valores])
        fila = ws.max_row
        ws.cell(row=fila, column=4).number_format = 'DD/MM/YYYY'
        ws.cell(row=fila, column=6).number_format = FORMATO_KG
        ws.cell(row=fila, column=7).number_format = FORMATO_PRECIO_KG
        for col in (8, 9, 10, 11):
            ws.cell(row=fila, column=col).number_format = FORMATO_DINERO
        if cancelado:
            for col in range(1, len(COLUMNAS_ESTADO) + 1):
                ws.cell(row=fila, column=col).font = tachado
                ws.cell(row=fila, column=col).fill = gris
    for col, ancho in zip('ABCDEFGHIJK', (12, 12, 40, 12, 20, 11, 12, 15, 15, 15, 16)):
        ws.column_dimensions[col].width = ancho
    ws.freeze_panes = f'A{encabezado + 1}'


# --- Saldos de clientes -----------------------------------------------------

def _columnas_saldos(empresas):
    return ['Cliente', *[e.nombre for e in empresas], 'Saldo conjunto', 'Último pago', 'Monto último pago', 'Celular', 'Pagaré']


def construir_pdf_saldos(empresas, filas, resumido=False, sin_identificar=None):
    """
    Completo: hoja horizontal con último pago, celular y pagaré. Resumido: hoja
    vertical solo con cliente, saldo por empresa y saldo conjunto.
    """
    buffer = BytesIO()
    documento = _documento(buffer, 'Saldos de clientes', horizontal=not resumido)
    estilos = _estilos()
    elementos = [
        Paragraph('Importadora y Exportadora de Mariscos de Centro América y el Caribe', estilos['empresa']),
        Paragraph('Saldos de clientes', estilos['titulo']),
        Paragraph(_generado(), estilos['generado']),
        Spacer(1, 0.4 * cm),
    ]
    columnas = len(empresas) + 2
    tabla_filas = [_columnas_saldos(empresas)[:columnas] if resumido else _columnas_saldos(empresas)]
    totales = {e.id: 0 for e in empresas}
    total_conjunto = 0
    for f in filas:
        fila = [
            _celda_detalle(f.cliente.nombre),
            *[_celda_detalle(_dinero(f.por_empresa[e.id]), TA_RIGHT) for e in empresas],
            _celda_detalle(_dinero(f.conjunto), TA_RIGHT),
            _celda_detalle(f.ultimo_pago[0].strftime('%d/%m/%Y') if f.ultimo_pago else '', TA_CENTER),
            _celda_detalle(_dinero(f.ultimo_pago[1]) if f.ultimo_pago and f.ultimo_pago[1] is not None else '', TA_RIGHT),
            _celda_detalle(f.perfil.celular if f.perfil else ''),
            _celda_detalle('Sí' if f.perfil and f.perfil.pagare else '', TA_CENTER),
        ]
        tabla_filas.append(fila[:columnas] if resumido else fila)
        for e in empresas:
            totales[e.id] += f.por_empresa[e.id]
        total_conjunto += f.conjunto
    if sin_identificar and any(sin_identificar.values()):
        conjunto_sin = sum(sin_identificar.values())
        fila = ['POR IDENTIFICAR', *[_dinero(sin_identificar.get(e.id, 0)) for e in empresas], _dinero(conjunto_sin), '', '', '', '']
        tabla_filas.append(fila[:columnas] if resumido else fila)
        for e in empresas:
            totales[e.id] += sin_identificar.get(e.id, 0)
        total_conjunto += conjunto_sin
    total = ['TOTAL', *[_dinero(totales[e.id]) for e in empresas], _dinero(total_conjunto), '', '', '', '']
    tabla_filas.append(total[:columnas] if resumido else total)
    if resumido:
        anchos = _anchos([8.1, *[3.5] * len(empresas), 3.5])
    else:
        anchos = _anchos([6.4, *[3.2] * len(empresas), 3.2, 2.2, 2.8, 4.2, 1.4], horizontal=True)
    tabla = Table(tabla_filas, colWidths=anchos, repeatRows=1)
    ultima = len(tabla_filas) - 1
    tabla.setStyle(_estilo_tabla([
        ('FONTNAME', (0, ultima), (-1, ultima), 'Helvetica-Bold'),
        ('ALIGN', (1, ultima), (len(empresas) + 1, ultima), 'RIGHT'),
        ('BACKGROUND', (0, ultima), (-1, ultima), colors.HexColor('#EEF5F5')),
    ]))
    elementos.append(tabla)
    documento.build(elementos, onFirstPage=_pie_pagina_pdf, onLaterPages=_pie_pagina_pdf)
    buffer.seek(0)
    return buffer


def construir_excel_saldos(empresas, filas, sin_identificar=None):
    wb = Workbook()
    ws = wb.active
    ws.title = 'Saldos de clientes'
    encabezados = _columnas_saldos(empresas)
    ws.append(encabezados)
    _estilizar_encabezado(ws, len(encabezados))
    for f in filas:
        ws.append([
            f.cliente.nombre,
            *[float(f.por_empresa[e.id]) for e in empresas],
            float(f.conjunto),
            f.ultimo_pago[0] if f.ultimo_pago else None,
            float(f.ultimo_pago[1]) if f.ultimo_pago and f.ultimo_pago[1] is not None else None,
            f.perfil.celular if f.perfil else '',
            'Sí' if f.perfil and f.perfil.pagare else '',
        ])
    if sin_identificar and any(sin_identificar.values()):
        ws.append(['POR IDENTIFICAR', *[float(sin_identificar.get(e.id, 0)) for e in empresas],
                   float(sum(sin_identificar.values()))])
    n = len(empresas)
    primera, ultima = 2, ws.max_row
    ws.append(['TOTAL', *[f'=SUM({ws.cell(row=1, column=c).column_letter}{primera}:'
                          f'{ws.cell(row=1, column=c).column_letter}{ultima})' for c in range(2, n + 3)]])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
    for fila in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for celda in fila[1:n + 2]:
            celda.number_format = FORMATO_DINERO
        fila[n + 2].number_format = 'DD/MM/YYYY'
        fila[n + 3].number_format = FORMATO_DINERO
    for fila in ws.iter_rows(min_row=ws.max_row, max_row=ws.max_row):
        for celda in fila:
            celda.font = Font(bold=True)
    ws.column_dimensions['A'].width = 42
    for c in range(2, len(encabezados) + 1):
        ws.column_dimensions[ws.cell(row=1, column=c).column_letter].width = 17
    ws.freeze_panes = 'B2'
    return wb


# --- Pagos de clientes ------------------------------------------------------

COLUMNAS_PAGOS = ['Fecha', 'Cliente', 'Empresa', 'Tipo', 'Monto', 'Aplicado a', 'Referencia', 'Capturó', 'Estado']


def _fila_pago(pago):
    return [
        pago.fecha,
        pago.cliente.nombre if pago.cliente_id else 'POR IDENTIFICAR',
        pago.empresa.nombre,
        pago.get_tipo_display(),
        pago.monto,
        texto_aplicacion(partes_aplicadas(pago), pago.monto) or 'Lo más antiguo',
        pago.referencia or '',
        pago.usuario.username if pago.usuario_id else '',
        f'Cancelado: {pago.motivo_cancelacion}' if pago.cancelado else 'Activo',
    ]


def _tabla_pagos(pagos, texto_vacio):
    """Tabla de pagos con su total; los cancelados en gris y fuera del total."""
    celda = ParagraphStyle('celda_pago', parent=ESTILOS['Normal'], fontSize=8, leading=9.5)
    filas = [COLUMNAS_PAGOS]
    extra = []
    total = Decimal('0')
    for pago in pagos:
        fecha, cliente, empresa, tipo, monto, aplicado, referencia, usuario, estado = _fila_pago(pago)
        c = pago.cancelado
        filas.append([
            _celda_detalle(fecha.strftime('%d/%m/%Y'), TA_CENTER, c), _celda_detalle(cliente, TA_LEFT, c),
            _celda_detalle(empresa, TA_LEFT, c), _celda_detalle(tipo, TA_LEFT, c),
            _celda_detalle(_dinero(monto), TA_RIGHT, c), _celda_detalle(aplicado, TA_LEFT, c),
            _celda_detalle(referencia, TA_LEFT, c),
            _celda_detalle(usuario, TA_LEFT, c), _celda_detalle(estado, TA_LEFT, c),
        ])
        if c:
            extra.append(('BACKGROUND', (0, len(filas) - 1), (-1, len(filas) - 1), COLOR_FILA_CANCELADA_RL))
        else:
            total += monto
    if len(filas) == 1:
        filas.append(['', Paragraph(texto_vacio, celda), '', '', '', '', '', '', ''])
    activos = sum(1 for p in pagos if not p.cancelado)
    filas.append(['', Paragraph(f'<b>Total ({activos} activo(s))</b>', celda), '', '', _dinero(total), '', '', '', ''])
    ultima = len(filas) - 1
    tabla = Table(filas, colWidths=_anchos([2.0, 4.8, 2.8, 3.0, 2.5, 4.6, 3.8, 1.9, 2.8], horizontal=True), repeatRows=1)
    tabla.setStyle(_estilo_tabla(extra + [
        ('FONTNAME', (0, ultima), (-1, ultima), 'Helvetica-Bold'),
        ('ALIGN', (4, ultima), (4, ultima), 'RIGHT'),
        ('BACKGROUND', (0, ultima), (-1, ultima), colors.HexColor('#EEF5F5')),
    ]))
    return tabla


def construir_pdf_pagos(pagos, filtros, por_identificar=None):
    """
    Los pagos de la pantalla con sus filtros. `por_identificar`: depósitos aún
    sin cliente, en su propia sección (no entran al total de los pagos).
    """
    buffer = BytesIO()
    documento = _documento(buffer, 'Pagos de clientes', horizontal=True)
    estilos = _estilos()
    seccion = ParagraphStyle('seccion', parent=ESTILOS['Heading2'], fontSize=11.5,
                             textColor=COLOR_ENCABEZADO_RL, spaceBefore=10, spaceAfter=4)
    elementos = [
        Paragraph('Importadora y Exportadora de Mariscos de Centro América y el Caribe', estilos['empresa']),
        Paragraph('Pagos de clientes', estilos['titulo']),
        *[Paragraph(escape(f), estilos['subtitulo']) for f in filtros],
        Paragraph(_generado(), estilos['generado']),
        Spacer(1, 0.4 * cm),
        _tabla_pagos(pagos, 'Sin pagos con estos filtros.'),
    ]
    if por_identificar is not None:
        elementos += [
            Paragraph('Depósitos por identificar', seccion),
            Paragraph('Pendientes a la fecha: aún no se sabe de qué cliente son, así que no bajan ningún saldo.',
                      estilos['subtitulo']),
            Spacer(1, 0.15 * cm),
            _tabla_pagos(por_identificar, 'No hay depósitos por identificar.'),
        ]
    documento.build(elementos, onFirstPage=_pie_pagina_pdf, onLaterPages=_pie_pagina_pdf)
    buffer.seek(0)
    return buffer


def _hoja_pagos(ws, pagos, filtros):
    ws.append(COLUMNAS_PAGOS)
    _estilizar_encabezado(ws, len(COLUMNAS_PAGOS))
    gris = PatternFill('solid', fgColor=COLOR_FILA_CANCELADA)
    tachado = Font(strike=True, color='808080')
    total = Decimal('0')
    for pago in pagos:
        valores = _fila_pago(pago)
        valores[4] = float(valores[4])
        ws.append(valores)
        fila = ws.max_row
        ws.cell(row=fila, column=1).number_format = 'DD/MM/YYYY'
        ws.cell(row=fila, column=5).number_format = FORMATO_DINERO
        # El texto capturado (cliente, nota, referencia) va siempre como texto, nunca como fórmula.
        for col, valor in enumerate(valores, start=1):
            if isinstance(valor, str):
                ws.cell(row=fila, column=col).data_type = 's'
        if pago.cancelado:
            for col in range(1, len(COLUMNAS_PAGOS) + 1):
                ws.cell(row=fila, column=col).font = tachado
                ws.cell(row=fila, column=col).fill = gris
        else:
            total += pago.monto
    ws.append([None, 'Total (activos)', None, None, float(total)])
    ws.cell(row=ws.max_row, column=2).font = Font(bold=True)
    ws.cell(row=ws.max_row, column=5).font = Font(bold=True)
    ws.cell(row=ws.max_row, column=5).number_format = FORMATO_DINERO
    ws.append([])
    for f in filtros:
        ws.append([f])
    for col, ancho in zip('ABCDEFGHI', (12, 36, 20, 26, 15, 40, 40, 14, 30)):
        ws.column_dimensions[col].width = ancho
    ws.freeze_panes = 'A2'


def construir_excel_pagos(pagos, filtros, por_identificar=None):
    wb = Workbook()
    ws = wb.active
    ws.title = 'Pagos de clientes'
    _hoja_pagos(ws, pagos, filtros)
    if por_identificar is not None:
        _hoja_pagos(wb.create_sheet('Por identificar'), por_identificar,
                    ['Depósitos por identificar pendientes a la fecha (no bajan ningún saldo).'])
    return wb
