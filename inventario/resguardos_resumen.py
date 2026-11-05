"""
Resumen de resguardos: lo que cada cliente tiene guardado hoy, como la hoja
RESUMEN RESG. del control del área (docs/CARTERA/CONTROL DE RESGUARDO A
CLIENTES 2026.xlsx). Un bloque por cliente con una fila por nota y producto
pendiente: fecha, nota, talla, proveedor, kilos por entregar, precio y total
(kilos × precio), y el total del cliente.

El proveedor sale de los lotes de los retiros ya hechos: la venta en
resguardo es por producto, sin lote. Sin entregas todavía, se toma el que
dejó en la nota de la línea la carga inicial (cargar_resguardos_pendientes).
"""
import re
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .formatos import FORMATO_KG, kg
from .models import SalidaDetalle
from .resguardos import CERO, lineas_con_retirado

CENTAVO = Decimal('0.01')
COLUMNAS = ['FECHA', 'NOTA', 'TALLA', 'PROVEEDOR', 'KILOS', 'PRECIO', 'TOTAL', 'OBSERVACIÓN']
FORMATO_PESOS = '"$"#,##0.00'
# Nota que cargar_resguardos_pendientes deja en cada línea de la carga inicial.
NOTA_PROVEEDOR = re.compile(r'Proveedor según control de resguardos:\s*(.+)')


def _proveedor_y_observacion(linea, entregados):
    """(proveedor, observación) de una línea de venta en resguardo."""
    notas = (linea.notas or '').strip()
    de_nota = NOTA_PROVEEDOR.fullmatch(notas)
    if entregados:
        return ' / '.join(entregados), '' if de_nota else notas
    if de_nota:
        return de_nota.group(1).strip(), ''
    return '', notas


def resumen_resguardos(cliente_id=None):
    """
    [{cliente: {id, nombre}, filas: [...], kilos, total, sin_precio}] por
    nombre de cliente; solo líneas con kilos por entregar. `total` suma solo
    las filas con precio; `sin_precio` cuenta las que no lo tienen.
    """
    lineas = (
        lineas_con_retirado()
        .filter(salida__cliente__isnull=False, salida__folio_retiro__isnull=True)
        .select_related('salida__cliente')
        .order_by('salida__cliente__nombre', 'salida__fecha', 'salida_id', 'id')
    )
    if cliente_id:
        lineas = lineas.filter(salida__cliente_id=cliente_id)
    lineas = [l for l in lineas if l.kilos_resguardo - l.kilos_retirados > 0]

    # Proveedores de lo ya entregado, por línea de venta (una consulta).
    proveedores = {}
    entregas = (
        SalidaDetalle.objects.filter(retiro_de__in=[l.id for l in lineas], entrada_detalle__proveedor_origen__isnull=False)
        .values_list('retiro_de_id', 'entrada_detalle__proveedor_origen__nombre')
        .order_by('salida__fecha', 'id')
    )
    for linea_id, nombre in entregas:
        nombres = proveedores.setdefault(linea_id, [])
        if nombre not in nombres:
            nombres.append(nombre)

    clientes = {}
    for linea in lineas:
        venta = linea.salida
        bloque = clientes.setdefault(venta.cliente_id, {
            'cliente': {'id': venta.cliente_id, 'nombre': venta.cliente.nombre},
            'filas': [], 'kilos': CERO, 'total': CERO, 'sin_precio': 0,
        })
        pendiente = linea.kilos_resguardo - linea.kilos_retirados
        precio = linea.precio_x_kilo if linea.precio_x_kilo else None
        total = (pendiente * precio).quantize(CENTAVO) if precio is not None else None
        proveedor, observacion = _proveedor_y_observacion(linea, proveedores.get(linea.id, []))
        bloque['filas'].append({
            'linea_id': linea.id,
            'venta_id': venta.id,
            'fecha': venta.fecha,
            'nota': venta.notas,
            'folio_de_salida': venta.folio_de_salida,
            'talla': f'{linea.producto.talla} {linea.producto.tipo}'.strip(),
            'proveedor': proveedor,
            'kilos': pendiente,
            'precio': precio,
            'total': total,
            'observacion': observacion,
        })
        bloque['kilos'] += pendiente
        if total is None:
            bloque['sin_precio'] += 1
        else:
            bloque['total'] += total
    return list(clientes.values())


def serializar_resumen(resumen):
    def texto(valor, decimales):
        return None if valor is None else str(Decimal(valor).quantize(Decimal(decimales)))

    return {
        'clientes': [
            {
                'cliente': b['cliente'],
                'kilos': texto(b['kilos'], '0.001'),
                'total': texto(b['total'], '0.01'),
                'sin_precio': b['sin_precio'],
                'filas': [
                    {
                        **{k: f[k] for k in ('linea_id', 'venta_id', 'nota', 'folio_de_salida', 'talla', 'proveedor', 'observacion')},
                        'fecha': f['fecha'].isoformat(),
                        'kilos': texto(f['kilos'], '0.001'),
                        'precio': texto(f['precio'], '0.0001'),
                        'total': texto(f['total'], '0.01'),
                    }
                    for f in b['filas']
                ],
            }
            for b in resumen
        ],
        'kilos': texto(sum((b['kilos'] for b in resumen), CERO), '0.001'),
        'total': texto(sum((b['total'] for b in resumen), CERO), '0.01'),
    }


def _texto(celda, valor):
    """Texto capturado por usuarios: siempre como cadena, nunca como fórmula (p. ej. una nota que empiece con =)."""
    celda.value = valor
    if isinstance(valor, str):
        celda.data_type = 's'
    return celda


def _generado():
    return f"Al {timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M')}"


# ---------------------------------------------------------------- Excel
def construir_excel(resumen):
    """Una hoja como RESUMEN RESG.: un bloque por cliente, con fórmulas de total."""
    wb = Workbook()
    ws = wb.active
    ws.title = 'RESUMEN RESG.'
    medio = Side(style='medium', color='000000')
    delgado = Side(style='thin', color='000000')
    borde = Border(left=delgado, right=delgado, top=delgado, bottom=delgado)
    borde_titulo = Border(left=medio, right=medio, top=medio, bottom=medio)
    gris = PatternFill('solid', fgColor='D9E1F2')
    centro = Alignment(horizontal='center', vertical='center')

    ws.cell(1, 2, f'Resumen de resguardos · {_generado()}').font = Font(italic=True, color='808080')
    fila = 3
    if not resumen:
        ws.cell(fila, 2, 'No hay resguardos pendientes.')
    for bloque in resumen:
        # Título del cliente (B:H combinadas) y OBSERVACIÓN aparte, como en la hoja.
        _texto(ws.cell(fila, 2), f"RESGUARDO {bloque['cliente']['nombre']}")
        ws.merge_cells(start_row=fila, start_column=2, end_row=fila, end_column=8)
        ws.cell(fila, 9, 'OBSERVACIÓN')
        for col in range(2, 10):
            celda = ws.cell(fila, col)
            celda.font = Font(bold=True, size=14)
            celda.fill = gris
            celda.alignment = centro
            celda.border = borde_titulo if col < 9 else borde
        fila += 1
        for col, titulo in enumerate(COLUMNAS[:7], start=2):
            celda = ws.cell(fila, col, titulo)
            celda.font = Font(bold=True)
            celda.fill = gris
            celda.alignment = centro
            celda.border = borde
        ws.cell(fila, 9).border = borde
        fila += 1
        primera = fila
        for f in bloque['filas']:
            nota = f['nota'] or (f"Salida {f['folio_de_salida']}" if f['folio_de_salida'] else '')
            valores = [f['fecha'], nota, f['talla'], f['proveedor'], float(f['kilos']),
                       float(f['precio']) if f['precio'] is not None else None]
            for col, valor in enumerate(valores, start=2):
                _texto(ws.cell(fila, col), valor).border = borde
            ws.cell(fila, 8, f'=F{fila}*G{fila}' if f['precio'] is not None else None).border = borde
            observacion = f['observacion'] or ('Sin precio en la venta' if f['precio'] is None else '')
            _texto(ws.cell(fila, 9), observacion).border = borde
            ws.cell(fila, 2).number_format = 'DD/MM/YYYY'
            ws.cell(fila, 6).number_format = FORMATO_KG
            ws.cell(fila, 7).number_format = FORMATO_PESOS
            ws.cell(fila, 8).number_format = FORMATO_PESOS
            fila += 1
        ultima = fila - 1
        fila += 1
        ws.cell(fila, 5, 'TOTAL').font = Font(bold=True, size=12)
        ws.cell(fila, 6, f'=SUM(F{primera}:F{ultima})').number_format = FORMATO_KG
        ws.cell(fila, 8, f'=SUM(H{primera}:H{ultima})').number_format = FORMATO_PESOS
        for col in (5, 6, 7, 8):
            ws.cell(fila, col).border = borde
            ws.cell(fila, col).fill = gris
            ws.cell(fila, col).font = Font(bold=True, size=12)
        fila += 4

    for col, ancho in zip('ABCDEFGHI', (4, 13, 18.5, 27, 26, 14.5, 13, 18.8, 39)):
        ws.column_dimensions[col].width = ancho
    buffer = BytesIO()
    wb.save(buffer)
    return buffer


# ---------------------------------------------------------------- PDF
def construir_pdf(resumen):
    buffer = BytesIO()
    documento = SimpleDocTemplate(
        buffer, pagesize=letter, leftMargin=1.2 * cm, rightMargin=1.2 * cm,
        topMargin=1.2 * cm, bottomMargin=1.2 * cm, title='Resumen de resguardos',
    )
    estilos = getSampleStyleSheet()
    celda = ParagraphStyle('celda', parent=estilos['Normal'], fontSize=7.5, leading=9)
    titulo_cliente = ParagraphStyle('cliente', parent=estilos['Normal'], fontSize=10.5, fontName='Helvetica-Bold')
    elementos = [
        Paragraph('Resumen de resguardos', estilos['Title']),
        Paragraph(f'Kilos por entregar a cada cliente y su valor a precio de venta. {_generado()}', estilos['Normal']),
        Spacer(1, 0.4 * cm),
    ]
    if not resumen:
        elementos.append(Paragraph('No hay resguardos pendientes.', estilos['Normal']))
    gris = colors.HexColor('#D9E1F2')
    anchos = [1.8, 2.4, 3.3, 3.4, 2.0, 1.7, 2.4, 2.2]
    escala = (21.59 - 2.4) / sum(anchos)
    for bloque in resumen:
        filas = [
            [Paragraph(escape(f"RESGUARDO {bloque['cliente']['nombre']}"), titulo_cliente)] + [''] * 7,
            COLUMNAS,
        ]
        for f in bloque['filas']:
            nota = f['nota'] or (f"Salida {f['folio_de_salida']}" if f['folio_de_salida'] else '')
            filas.append([
                f['fecha'].strftime('%d/%m/%Y'), Paragraph(escape(nota), celda), Paragraph(escape(f['talla']), celda),
                Paragraph(escape(f['proveedor'] or '—'), celda), kg(f['kilos']),
                f"${f['precio']:,.2f}" if f['precio'] is not None else 'Sin precio',
                f"${f['total']:,.2f}" if f['total'] is not None else '',
                Paragraph(escape(f['observacion']), celda),
            ])
        filas.append(['', '', '', 'TOTAL', kg(bloque['kilos']), '', f"${bloque['total']:,.2f}", ''])
        ultima = len(filas) - 1
        tabla = Table(filas, colWidths=[a * escala * cm for a in anchos], repeatRows=2)
        tabla.setStyle(TableStyle([
            ('SPAN', (0, 0), (-1, 0)),
            ('BACKGROUND', (0, 0), (-1, 1), gris),
            ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 1), (-1, -1), 7.5),
            ('ALIGN', (0, 1), (-1, 1), 'CENTER'),
            ('ALIGN', (4, 2), (6, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTNAME', (0, ultima), (-1, ultima), 'Helvetica-Bold'),
            ('BACKGROUND', (3, ultima), (6, ultima), gris),
        ]))
        elementos += [KeepTogether([tabla]), Spacer(1, 0.5 * cm)]
    documento.build(elementos)
    buffer.seek(0)
    return buffer
