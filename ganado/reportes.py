"""
Reportes de Ganado (pantalla, PDF y Excel):

- Inventario a una fecha: cabezas por rancho, especie y categoría (hembras,
  machos, con y sin arete) y la lista de animales de cada rancho con su
  arete en 3 columnas, como las hojas del área.
- Movimientos de un periodo: balance por rancho (inicial + altas + traspasos
  recibidos − enviados − bajas = final) y los movimientos.

La categoría es la actual del animal (sus cambios quedan en su historial).
"""
from collections import Counter, defaultdict
from datetime import timedelta
from io import BytesIO
from xml.sax.saxutils import escape

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from treasury.reportes import (
    COLOR_ENCABEZADO, COLOR_ENCABEZADO_RL, ESTILOS, _celda_detalle, _estilizar_encabezado, _pie_pagina_pdf,
)

from .models import (
    ACTIVO, ALTA, BAJA, COMPRA, HEMBRA, MUERTE, NACIMIENTO, REGALO, VENTA,
    Animal, Movimiento, Rancho,
)
from .servicio import ubicacion_a_fecha

EMPRESA = 'Importadora y Exportadora de Mariscos de Centro América y el Caribe'
FORMATO_DINERO = '#,##0.00'


def _texto(valor):
    """Celda de texto en Excel: nunca se interpreta como fórmula."""
    return '' if valor is None else str(valor)


# --- Inventario -------------------------------------------------------------

def inventario(fecha, rancho_id=None, especie_id=None):
    ubicacion = ubicacion_a_fecha(fecha)
    activos = {a: r for a, (r, estado) in ubicacion.items() if estado == ACTIVO}
    animales = Animal.objects.filter(pk__in=activos).select_related('especie', 'categoria')
    if especie_id:
        animales = animales.filter(especie_id=especie_id)
    ranchos = Rancho.objects.filter(pk__in=set(activos.values()))
    if rancho_id:
        ranchos = ranchos.filter(pk=rancho_id)
    ranchos = {r.pk: r for r in ranchos}

    por_rancho = defaultdict(list)
    for animal in animales:
        rancho = activos[animal.pk]
        if rancho in ranchos:
            por_rancho[rancho].append(animal)

    bloques = []
    for rancho in sorted(ranchos.values(), key=lambda r: r.nombre):
        lista = sorted(por_rancho.get(rancho.pk, []), key=lambda a: (
            a.especie.nombre, a.categoria.orden, a.categoria.nombre, a.sexo, not a.arete, a.arete_2, a.arete_3, a.pk,
        ))
        if not lista:
            continue
        filas = {}
        for a in lista:
            clave = (a.especie.nombre, a.categoria.orden, a.categoria.nombre)
            fila = filas.setdefault(clave, {
                'especie': a.especie.nombre, 'categoria': a.categoria.nombre,
                'hembras': 0, 'machos': 0, 'con_arete': 0, 'sin_arete': 0, 'total': 0,
            })
            fila['hembras' if a.sexo == HEMBRA else 'machos'] += 1
            fila['con_arete' if a.arete else 'sin_arete'] += 1
            fila['total'] += 1
        bloques.append({
            'rancho': {'id': rancho.pk, 'nombre': rancho.nombre},
            'filas': [filas[c] for c in sorted(filas)],
            'animales': lista,
            'total': len(lista),
        })

    totales = Counter()
    for bloque in bloques:
        for fila in bloque['filas']:
            for campo in ('hembras', 'machos', 'con_arete', 'sin_arete', 'total'):
                totales[campo] += fila[campo]
    return {'fecha': fecha, 'ranchos': bloques, 'totales': dict(totales)}


def serializar_inventario(datos, con_animales=False):
    ranchos = []
    for bloque in datos['ranchos']:
        r = {k: v for k, v in bloque.items() if k != 'animales'}
        if con_animales:
            r['animales'] = [_animal(a) for a in bloque['animales']]
        ranchos.append(r)
    return {'fecha': datos['fecha'].isoformat(), 'ranchos': ranchos, 'totales': datos['totales']}


def _animal(a):
    return {
        'id': a.pk, 'folio': a.folio, 'arete_1': a.arete_1, 'arete_2': a.arete_2, 'arete_3': a.arete_3,
        'especie': a.especie.nombre, 'categoria': a.categoria.nombre, 'sexo': a.sexo, 'raza': a.raza,
        'fecha_nacimiento': a.fecha_nacimiento.isoformat() if a.fecha_nacimiento else None, 'notas': a.notas,
    }


COLUMNAS_RESUMEN = ['Rancho', 'Especie', 'Categoría', 'Hembras', 'Machos', 'Con arete', 'Sin arete', 'Total']
COLUMNAS_ANIMALES = ['Categoría', 'Sexo', 'Arete', '', '', 'Folio', 'Raza / color', 'Nacimiento', 'Notas']


def excel_inventario(datos):
    wb = Workbook()
    ws = wb.active
    ws.title = 'RESUMEN'
    ws.append(COLUMNAS_RESUMEN)
    _estilizar_encabezado(ws, len(COLUMNAS_RESUMEN))
    negrita = Font(bold=True)
    filas_subtotal = []
    for bloque in datos['ranchos']:
        inicio = ws.max_row + 1
        for f in bloque['filas']:
            ws.append([_texto(bloque['rancho']['nombre']), _texto(f['especie']), _texto(f['categoria']),
                       f['hembras'], f['machos'], f['con_arete'], f['sin_arete'], f['total']])
        fin = ws.max_row
        ws.append([_texto(f"Total {bloque['rancho']['nombre']}"), '', '',
                   *[f'=SUM({c}{inicio}:{c}{fin})' for c in 'DEFGH']])
        filas_subtotal.append(ws.max_row)
        for celda in ws[ws.max_row]:
            celda.font = negrita
    if filas_subtotal:
        ws.append(['TOTAL GENERAL', '', '', *[
            '=' + '+'.join(f'{c}{n}' for n in filas_subtotal) for c in 'DEFGH'
        ]])
        for celda in ws[ws.max_row]:
            celda.font = negrita
    for col, ancho in zip('ABCDEFGH', (24, 12, 18, 10, 10, 11, 11, 10)):
        ws.column_dimensions[col].width = ancho
    ws.freeze_panes = 'A2'

    for bloque in datos['ranchos']:
        hoja = wb.create_sheet(_nombre_hoja(bloque['rancho']['nombre']))
        hoja.append([_texto(bloque['rancho']['nombre']), '', '', '', '',
                     f"Al {datos['fecha']:%d/%m/%Y}", '', '', f"{bloque['total']} cabezas"])
        hoja['A1'].font = Font(bold=True, size=13)
        hoja.append(COLUMNAS_ANIMALES)
        hoja.merge_cells('C2:E2')
        for col in range(1, len(COLUMNAS_ANIMALES) + 1):
            hoja.cell(row=2, column=col).font = Font(bold=True, color='FFFFFF')
            hoja.cell(row=2, column=col).fill = PatternFill('solid', fgColor=COLOR_ENCABEZADO)
        for a in bloque['animales']:
            hoja.append([_texto(a.categoria.nombre), a.get_sexo_display(), _texto(a.arete_1), _texto(a.arete_2),
                         _texto(a.arete_3) if a.arete else 'S/A', a.folio, _texto(a.raza),
                         a.fecha_nacimiento, _texto(a.notas)])
            hoja.cell(row=hoja.max_row, column=8).number_format = 'DD/MM/YYYY'
        for col in (1, 3, 4, 5, 7, 9):
            for fila in hoja.iter_rows(min_row=3, min_col=col, max_col=col):
                fila[0].data_type = 's'
        for col, ancho in zip('ABCDEFGHI', (16, 9, 7, 7, 7, 11, 16, 12, 40)):
            hoja.column_dimensions[col].width = ancho
        hoja.freeze_panes = 'A3'
    return wb


def _nombre_hoja(nombre):
    limpio = ''.join(c for c in nombre if c not in '[]:*?/\\')
    return limpio[:31] or 'RANCHO'


def _estilos():
    return {
        'empresa': ParagraphStyle('empresa', parent=ESTILOS['Heading1'], fontSize=14,
                                  textColor=COLOR_ENCABEZADO_RL, spaceAfter=1),
        'titulo': ParagraphStyle('titulo', parent=ESTILOS['Normal'], fontSize=12, spaceAfter=3),
        'rancho': ParagraphStyle('rancho', parent=ESTILOS['Normal'], fontSize=10.5, spaceBefore=8, spaceAfter=3,
                                 fontName='Helvetica-Bold'),
        'generado': ParagraphStyle('generado', parent=ESTILOS['Normal'], fontSize=8,
                                   textColor=colors.HexColor('#999999')),
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


def _generado():
    return f"Generado el {timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M')}"


def _documento(buffer, titulo, horizontal=False):
    return SimpleDocTemplate(
        buffer, pagesize=landscape(letter) if horizontal else letter, title=titulo,
        leftMargin=1.2 * cm, rightMargin=1.2 * cm, topMargin=1.2 * cm, bottomMargin=1.2 * cm,
    )


def _total(fila_numeros, etiqueta, columnas_texto):
    return [_celda_detalle(etiqueta), *[''] * (columnas_texto - 1),
            *[_celda_detalle(str(n), TA_RIGHT) for n in fila_numeros]]


def pdf_inventario(datos, filtros=''):
    buffer = BytesIO()
    documento = _documento(buffer, 'Inventario de ganado')
    estilos = _estilos()
    elementos = [
        Paragraph(EMPRESA, estilos['empresa']),
        Paragraph(f"Inventario de ganado al {datos['fecha']:%d/%m/%Y}", estilos['titulo']),
    ]
    if filtros:
        elementos.append(Paragraph(escape(filtros), estilos['generado']))
    elementos += [Paragraph(_generado(), estilos['generado']), Spacer(1, 0.3 * cm)]
    campos = ('hembras', 'machos', 'con_arete', 'sin_arete', 'total')
    anchos = [3.0, 4.2, 2.0, 2.0, 2.2, 2.2, 2.0]
    negrita = [('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
               ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#F2F2F2'))]
    for bloque in datos['ranchos']:
        elementos.append(Paragraph(f"{escape(bloque['rancho']['nombre'])} — {bloque['total']} cabezas", estilos['rancho']))
        filas = [['Especie', 'Categoría', 'Hembras', 'Machos', 'Con arete', 'Sin arete', 'Total']]
        for f in bloque['filas']:
            filas.append([_celda_detalle(f['especie']), _celda_detalle(f['categoria']),
                          *[_celda_detalle(str(f[c]), TA_RIGHT) for c in campos]])
        filas.append(_total([sum(f[c] for f in bloque['filas']) for c in campos], 'Total', 2))
        tabla = Table(filas, colWidths=[a * cm for a in anchos], repeatRows=1)
        tabla.setStyle(_estilo_tabla(negrita))
        elementos.append(tabla)
    if not datos['ranchos']:
        elementos.append(Paragraph('Sin cabezas a esa fecha.', ESTILOS['Normal']))
    elif len(datos['ranchos']) > 1:
        elementos.append(Paragraph('Total general', estilos['rancho']))
        filas = [['', '', 'Hembras', 'Machos', 'Con arete', 'Sin arete', 'Total'],
                 _total([datos['totales'].get(c, 0) for c in campos], 'Todos los ranchos', 2)]
        tabla = Table(filas, colWidths=[a * cm for a in anchos])
        tabla.setStyle(_estilo_tabla(negrita))
        elementos.append(tabla)
    documento.build(elementos, onFirstPage=_pie_pagina_pdf, onLaterPages=_pie_pagina_pdf)
    buffer.seek(0)
    return buffer


# --- Movimientos ------------------------------------------------------------

CONCEPTOS_BALANCE = [
    ('inicial', 'Inicial'), ('compras', 'Compras'), ('nacimientos', 'Nacimientos'), ('regalos', 'Regalos'),
    ('recibidos', 'Traspasos recibidos'), ('enviados', 'Traspasos enviados'), ('ventas', 'Ventas'),
    ('muertes', 'Muertes'), ('final', 'Final'),
]
ALTAS = {COMPRA: 'compras', NACIMIENTO: 'nacimientos', REGALO: 'regalos'}
BAJAS = {VENTA: 'ventas', MUERTE: 'muertes'}


def movimientos(desde, hasta, rancho_id=None, tipo=None, motivo=None):
    """Balance de cabezas por rancho en el periodo y la lista de movimientos (sin cancelados)."""
    lista = (Movimiento.objects
             .filter(cancelado=False, fecha__gte=desde, fecha__lte=hasta)
             .select_related('rancho', 'rancho_destino', 'usuario')
             .prefetch_related('lineas__animal__categoria', 'lineas__animal__especie')
             .order_by('fecha', 'id'))

    inicial = Counter(r for r, e in ubicacion_a_fecha(desde - timedelta(days=1)).values() if e == ACTIVO)
    final = Counter(r for r, e in ubicacion_a_fecha(hasta).values() if e == ACTIVO)
    balance = defaultdict(Counter)
    for rancho, n in inicial.items():
        balance[rancho]['inicial'] = n
    for rancho, n in final.items():
        balance[rancho]['final'] = n
    for m in lista:
        cabezas = len(m.lineas.all())
        if m.tipo == ALTA:
            balance[m.rancho_id][ALTAS[m.motivo]] += cabezas
        elif m.tipo == BAJA:
            balance[m.rancho_id][BAJAS[m.motivo]] += cabezas
        else:
            balance[m.rancho_id]['enviados'] += cabezas
            balance[m.rancho_destino_id]['recibidos'] += cabezas

    if rancho_id:
        lista = [m for m in lista if rancho_id in (m.rancho_id, m.rancho_destino_id)]
        balance = {rancho_id: balance.get(rancho_id, Counter())}
    if tipo:
        lista = [m for m in lista if m.tipo == tipo]
    if motivo:
        lista = [m for m in lista if m.motivo == motivo]

    nombres = dict(Rancho.objects.filter(pk__in=balance).values_list('pk', 'nombre'))
    filas_balance = [
        {'rancho': {'id': r, 'nombre': nombres[r]}, **{c: balance[r][c] for c, _ in CONCEPTOS_BALANCE}}
        for r in sorted(nombres, key=nombres.get)
        if any(balance[r].values())
    ]
    return {'desde': desde, 'hasta': hasta, 'balance': filas_balance, 'movimientos': list(lista)}


def _destino_texto(m):
    return m.rancho_destino.nombre if m.rancho_destino else ''


def _resumen(m):
    return ', '.join(f'{n} {c}' for c, n in Counter(l.animal.categoria.nombre for l in m.lineas.all()).items())


def _identificaciones(m):
    return ', '.join(l.animal.identificacion for l in m.lineas.all())


COLUMNAS_BALANCE = ['Rancho', *[t for _, t in CONCEPTOS_BALANCE]]
COLUMNAS_MOVIMIENTOS = ['Fecha', 'Folio', 'Tipo', 'Motivo', 'Rancho', 'Destino', 'Cabezas', 'Resumen',
                        'Contraparte', 'Importe', 'Notas']
COLUMNAS_DETALLE = ['Fecha', 'Folio', 'Tipo', 'Motivo', 'Rancho', 'Destino', 'Arete', '', '', 'Folio animal',
                    'Especie', 'Categoría', 'Sexo']


def excel_movimientos(datos):
    wb = Workbook()
    ws = wb.active
    ws.title = 'BALANCE'
    ws.append(COLUMNAS_BALANCE)
    _estilizar_encabezado(ws, len(COLUMNAS_BALANCE))
    for f in datos['balance']:
        ws.append([_texto(f['rancho']['nombre']), *[f[c] for c, _ in CONCEPTOS_BALANCE]])
    if datos['balance']:
        fin = ws.max_row
        ws.append(['TOTAL', *[f'=SUM({get_column_letter(i)}2:{get_column_letter(i)}{fin})'
                              for i in range(2, len(COLUMNAS_BALANCE) + 1)]])
        for celda in ws[ws.max_row]:
            celda.font = Font(bold=True)
    ws.column_dimensions['A'].width = 24
    for i in range(2, len(COLUMNAS_BALANCE) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 13

    hoja = wb.create_sheet('MOVIMIENTOS')
    hoja.append(COLUMNAS_MOVIMIENTOS)
    _estilizar_encabezado(hoja, len(COLUMNAS_MOVIMIENTOS))
    for m in datos['movimientos']:
        hoja.append([m.fecha, m.folio, m.get_tipo_display(), m.get_motivo_display() if m.motivo else '',
                     _texto(m.rancho.nombre), _texto(_destino_texto(m)), len(m.lineas.all()), _texto(_resumen(m)),
                     _texto(m.contraparte), float(m.importe) if m.importe is not None else None, _texto(m.notas)])
        fila = hoja.max_row
        hoja.cell(row=fila, column=1).number_format = 'DD/MM/YYYY'
        hoja.cell(row=fila, column=10).number_format = FORMATO_DINERO
        for col in (5, 6, 8, 9, 11):
            hoja.cell(row=fila, column=col).data_type = 's'
    for col, ancho in zip('ABCDEFGHIJK', (11, 10, 10, 12, 18, 18, 9, 30, 22, 13, 40)):
        hoja.column_dimensions[col].width = ancho
    hoja.freeze_panes = 'A2'

    detalle = wb.create_sheet('DETALLE')
    detalle.append(COLUMNAS_DETALLE)
    _estilizar_encabezado(detalle, len(COLUMNAS_DETALLE))
    detalle.merge_cells('G1:I1')
    for m in datos['movimientos']:
        for linea in m.lineas.all():
            a = linea.animal
            detalle.append([m.fecha, m.folio, m.get_tipo_display(), m.get_motivo_display() if m.motivo else '',
                            _texto(m.rancho.nombre), _texto(_destino_texto(m)), _texto(a.arete_1), _texto(a.arete_2),
                            _texto(a.arete_3) if a.arete else 'S/A', a.folio, _texto(a.especie.nombre),
                            _texto(a.categoria.nombre), a.get_sexo_display()])
            fila = detalle.max_row
            detalle.cell(row=fila, column=1).number_format = 'DD/MM/YYYY'
            for col in (5, 6, 7, 8, 9, 11, 12):
                detalle.cell(row=fila, column=col).data_type = 's'
    for col, ancho in zip('ABCDEFGHIJKLM', (11, 10, 10, 12, 18, 18, 7, 7, 7, 11, 11, 16, 9)):
        detalle.column_dimensions[col].width = ancho
    detalle.freeze_panes = 'A2'
    return wb


def pdf_movimientos(datos, filtros=''):
    buffer = BytesIO()
    documento = _documento(buffer, 'Movimientos de ganado', horizontal=True)
    estilos = _estilos()
    elementos = [
        Paragraph(EMPRESA, estilos['empresa']),
        Paragraph(f"Movimientos de ganado del {datos['desde']:%d/%m/%Y} al {datos['hasta']:%d/%m/%Y}", estilos['titulo']),
    ]
    if filtros:
        elementos.append(Paragraph(escape(filtros), estilos['generado']))
    elementos += [Paragraph(_generado(), estilos['generado']), Paragraph('Balance de cabezas', estilos['rancho'])]

    filas = [[Paragraph(f'<b>{t}</b>', ParagraphStyle('enc', parent=ESTILOS['Normal'], fontSize=7.5,
                                                       textColor=colors.white, alignment=TA_CENTER))
              for t in COLUMNAS_BALANCE]]
    for f in datos['balance']:
        filas.append([_celda_detalle(f['rancho']['nombre']),
                      *[_celda_detalle(str(f[c]), TA_RIGHT) for c, _ in CONCEPTOS_BALANCE]])
    if len(datos['balance']) > 1:
        filas.append(_total([sum(f[c] for f in datos['balance']) for c, _ in CONCEPTOS_BALANCE], 'Total', 1))
    if len(filas) == 1:
        filas.append([_celda_detalle('Sin cabezas en el periodo.'), *[''] * len(CONCEPTOS_BALANCE)])
    extra = [('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold')] if len(datos['balance']) > 1 else []
    tabla = Table(filas, colWidths=[4.6 * cm] + [2.3 * cm] * len(CONCEPTOS_BALANCE), repeatRows=1)
    tabla.setStyle(_estilo_tabla(extra))
    elementos.append(tabla)

    elementos.append(Paragraph('Movimientos', estilos['rancho']))
    filas = [['Fecha', 'Folio', 'Movimiento', 'Rancho', 'Destino', 'Cab.', 'Animales', 'Contraparte', 'Notas']]
    for m in datos['movimientos']:
        movimiento = m.get_tipo_display() + (f' · {m.get_motivo_display()}' if m.motivo else '')
        animales = f'{_resumen(m)}: {_identificaciones(m)}'
        contraparte = m.contraparte + (f' (${m.importe:,.2f})' if m.importe is not None else '')
        filas.append([
            _celda_detalle(f'{m.fecha:%d/%m/%Y}', TA_CENTER), _celda_detalle(m.folio), _celda_detalle(movimiento),
            _celda_detalle(m.rancho.nombre), _celda_detalle(_destino_texto(m)),
            _celda_detalle(str(len(m.lineas.all())), TA_RIGHT), _celda_detalle(animales, TA_LEFT),
            _celda_detalle(contraparte), _celda_detalle(m.notas),
        ])
    if len(filas) == 1:
        filas.append([_celda_detalle('Sin movimientos en el periodo.'), *[''] * 8])
    anchos = [1.8, 1.7, 2.6, 2.8, 2.8, 1.0, 7.5, 3.2, 2.8]
    tabla = Table(filas, colWidths=[a * cm for a in anchos], repeatRows=1)
    tabla.setStyle(_estilo_tabla())
    elementos.append(tabla)
    documento.build(elementos, onFirstPage=_pie_pagina_pdf, onLaterPages=_pie_pagina_pdf)
    buffer.seek(0)
    return buffer


def serializar_movimientos(datos):
    return {
        'desde': datos['desde'].isoformat(),
        'hasta': datos['hasta'].isoformat(),
        'balance': datos['balance'],
        'movimientos': [{
            'id': m.pk, 'folio': m.folio, 'fecha': m.fecha.isoformat(), 'tipo': m.tipo,
            'tipo_nombre': m.get_tipo_display(), 'motivo': m.motivo,
            'motivo_nombre': m.get_motivo_display() if m.motivo else '',
            'rancho': m.rancho.nombre, 'rancho_destino': _destino_texto(m), 'cabezas': len(m.lineas.all()),
            'resumen': _resumen(m), 'animales': _identificaciones(m), 'contraparte': m.contraparte,
            'importe': None if m.importe is None else f'{m.importe:.2f}', 'notas': m.notas,
        } for m in datos['movimientos']],
    }

