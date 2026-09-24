from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from django.db.models import DecimalField, F, IntegerField, Q, Sum, Value
from django.db.models.functions import Cast, Coalesce, Floor
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .caducidad import SIN_CADUCIDAD, rango_de_nivel
from .models import Camara, EntradaDetalle

COLOR_ENCABEZADO = "0E6B6F"
COLOR_ENCABEZADO_RL = colors.HexColor(f"#{COLOR_ENCABEZADO}")

SIN_PROVEEDOR = "Sin proveedor"
# En el PDF una tabla no puede crecer de ancho sin límite: si hay más
# proveedores que esto, los que pesan menos se agrupan en "Otros" para que
# la hoja siga siendo legible en landscape. El Excel no tiene este límite —
# ahí se exporta el detalle completo, sin agrupar, porque el usuario puede
# hacer zoom/scroll y no depende de caber en una hoja impresa.
MAX_COLUMNAS_PROVEEDOR_PDF = 8


def obtener_lotes_filtrados(params, ids_extra=None):
    """
    params: QueryDict (request.query_params). Devuelve un queryset de
    EntradaDetalle —un lote con existencia disponible— con cajas_disp y
    kilos_disp calculados a nivel de base de datos (no como property en
    Python) para que el reporte no dispare una consulta extra por cada lote
    cuando hay muchos registros. Se llaman distinto a las properties del
    modelo (cajas_disponibles/kilos_disponibles) porque Django no puede
    hidratar una annotation sobre un nombre que ya es una property sin
    setter — truena con AttributeError al iterar el queryset.

    cajas_disp es el piso de kilos_disp / peso_por_caja (mismo criterio que
    EntradaDetalle.cajas_disponibles, ver su docstring) y no un contador
    independiente, para que nunca se desincronice de kilos_disp.
    """
    lotes = (
        EntradaDetalle.objects
        .select_related(
            'producto', 'camara', 'proveedor_origen', 'lote_general',
            # documento_origen lee la entrada de cada lote; sin esto es una
            # consulta por lote. La cadena de movimientos NO se trae aquí a
            # propósito: solo la recorren los lotes sin proveedor (los que
            # llegaron por un traslado), y arrastrarla sumaba ~40 columnas a
            # cada fila de una consulta que ya devuelve miles.
            'entrada__proveedor',
        )
        .annotate(
            kilos_vendidos=Coalesce(
                Sum('salidas_detalle__total_kilos'), Value(Decimal('0')),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        )
        .annotate(
            kilos_disp=F('total_kilos') - F('kilos_vendidos'),
        )
        .annotate(
            cajas_disp=Cast(Floor(F('kilos_disp') / F('peso_por_caja')), IntegerField()),
        )
    )

    # ids_extra deja pasar lotes ya agotados: los usa la edición de una salida,
    # que debe seguir viendo los lotes que ella misma consumió.
    #
    # El criterio de "tiene existencia" es kilos_disp, no cajas_disp: un lote
    # puede quedar con menos peso del que pesa una caja completa (p. ej. tras
    # vender kilos sueltos de una caja ya abierta) y sigue siendo mercancía
    # real vendible por kilo, aunque ya no alcance para ofrecer una caja entera.
    condicion = Q(kilos_disp__gt=0)
    if ids_extra:
        condicion |= Q(id__in=ids_extra)
    lotes = lotes.filter(condicion)

    # Los filtros son los mismos chips de la pantalla de Existencias, con los
    # mismos nombres: así el PDF que se descarga corresponde exactamente a lo
    # que el usuario tiene en pantalla y no a "todo el inventario".
    camara_id = params.get('camara')
    if camara_id:
        lotes = lotes.filter(camara_id=camara_id)

    proveedor = (params.get('proveedor') or '').strip()
    if proveedor:
        # La pantalla muestra "—" en los lotes sin proveedor de origen.
        if proveedor == '—':
            lotes = lotes.filter(proveedor_origen__isnull=True)
        else:
            lotes = lotes.filter(proveedor_origen__nombre=proveedor)

    talla = (params.get('talla') or '').strip()
    if talla:
        lotes = lotes.filter(producto__talla=talla)

    tipo = (params.get('tipo') or '').strip()
    if tipo:
        lotes = lotes.filter(producto__tipo=tipo)

    nivel = (params.get('nivel') or '').strip()
    if nivel == SIN_CADUCIDAD:
        lotes = lotes.filter(fecha_caducidad__isnull=True)
    elif nivel:
        rango = rango_de_nivel(nivel, timezone.localdate())
        if rango:
            desde, hasta = rango
            lotes = lotes.filter(fecha_caducidad__isnull=False)
            if desde is not None:
                lotes = lotes.filter(fecha_caducidad__gte=desde)
            if hasta is not None:
                lotes = lotes.filter(fecha_caducidad__lte=hasta)

    busqueda = (params.get('busqueda') or '').strip()
    if busqueda:
        # Mismos campos que el buscador de la pantalla. Factura y recibo se
        # buscan sobre la entrada propia del lote: para los lotes llegados por
        # traslado la pantalla muestra el documento del lote de origen, que se
        # resuelve recorriendo la cadena en Python y no es filtrable en SQL.
        lotes = lotes.filter(
            Q(producto__talla__icontains=busqueda)
            | Q(producto__tipo__icontains=busqueda)
            | Q(lote_proveedor__icontains=busqueda)
            | Q(entrada__factura__icontains=busqueda)
            | Q(lote_general__codigo__icontains=busqueda)
        )

    return lotes.order_by('producto__tipo', 'producto__talla', 'camara__nombre')


def describir_filtros(params):
    """Los filtros aplicados, en texto, para imprimirlos en el encabezado del PDF."""
    partes = []

    camara_id = params.get('camara')
    if camara_id:
        camara = Camara.objects.filter(pk=camara_id).first()
        partes.append(f"Cámara: {camara.nombre}" if camara else f"Cámara #{camara_id}")

    for clave, etiqueta in (('proveedor', 'Proveedor'), ('talla', 'Talla'), ('tipo', 'Tipo')):
        valor = (params.get(clave) or '').strip()
        if valor:
            partes.append(f"{etiqueta}: {valor}")

    nivel = (params.get('nivel') or '').strip()
    if nivel:
        etiquetas = {
            'vencido': 'Vencido', 'critico': 'Crítico', 'urgente': 'Urgente',
            'por_vencer': 'Por vencer', SIN_CADUCIDAD: 'Sin caducidad',
        }
        partes.append(f"Caducidad: {etiquetas.get(nivel, nivel)}")

    busqueda = (params.get('busqueda') or '').strip()
    if busqueda:
        partes.append(f'Contiene: "{busqueda}"')

    return partes


def construir_pivote(lotes, max_columnas=None):
    """
    Agrupa los lotes ya filtrados por (talla, tipo) x proveedor de origen,
    sumando cajas/kilos disponibles. Si max_columnas está definido y hay más
    proveedores que ese número, los de menor total se agrupan en "Otros"
    para no romper el ancho de una hoja impresa.
    """
    filas = {}
    totales_proveedor = {}

    for lote in lotes:
        proveedor_nombre = lote.proveedor_origen.nombre if lote.proveedor_origen_id else SIN_PROVEEDOR
        clave = (lote.producto.talla, lote.producto.tipo)
        fila = filas.setdefault(clave, {'talla': lote.producto.talla, 'tipo': lote.producto.tipo, 'por_proveedor': {}})
        celda = fila['por_proveedor'].setdefault(proveedor_nombre, {'cajas': 0, 'kilos': Decimal('0')})
        celda['cajas'] += lote.cajas_disp
        celda['kilos'] += lote.kilos_disp

        totales_proveedor.setdefault(proveedor_nombre, {'cajas': 0, 'kilos': Decimal('0')})
        totales_proveedor[proveedor_nombre]['cajas'] += lote.cajas_disp
        totales_proveedor[proveedor_nombre]['kilos'] += lote.kilos_disp

    proveedores = sorted(
        totales_proveedor.keys(),
        key=lambda nombre: (nombre == SIN_PROVEEDOR, -totales_proveedor[nombre]['cajas'], nombre),
    )

    if max_columnas and len(proveedores) > max_columnas:
        # Los primeros (max_columnas - 1) por volumen se quedan como columna
        # propia; el resto —incluido "Sin proveedor" si no entró ya— se
        # colapsa en una sola columna "Otros".
        principales = [p for p in proveedores if p != SIN_PROVEEDOR][:max_columnas - 1]
        otros = [p for p in proveedores if p not in principales]

        for fila in filas.values():
            if not otros:
                continue
            celda_otros = {'cajas': 0, 'kilos': Decimal('0')}
            for nombre in otros:
                celda = fila['por_proveedor'].pop(nombre, None)
                if celda:
                    celda_otros['cajas'] += celda['cajas']
                    celda_otros['kilos'] += celda['kilos']
            if celda_otros['cajas'] or celda_otros['kilos']:
                fila['por_proveedor']['Otros'] = celda_otros

        proveedores = principales + (['Otros'] if otros else [])

    filas_ordenadas = sorted(filas.values(), key=lambda f: (f['tipo'], f['talla']))
    return filas_ordenadas, proveedores


def _estilizar_encabezado(ws, num_columnas):
    for col in range(1, num_columnas + 1):
        celda = ws.cell(row=1, column=col)
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor=COLOR_ENCABEZADO)
        celda.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 20


def construir_libro_excel_lote(lotes):
    wb = Workbook()
    ws = wb.active
    ws.title = "Existencias por lote"
    encabezados = [
        "Cámara", "Proveedor", "Talla", "Tipo", "Lote proveedor", "Recibo ingreso",
        "Cajas disponibles", "Kilos disponibles", "Costo/kg",
    ]
    ws.append(encabezados)
    _estilizar_encabezado(ws, len(encabezados))

    for lote in lotes:
        ws.append([
            lote.camara.nombre if lote.camara_id else "Venta directa (sin cámara)",
            lote.proveedor_origen.nombre if lote.proveedor_origen_id else "—",
            lote.producto.talla,
            lote.producto.tipo,
            lote.lote_proveedor,
            lote.lote_general.codigo if lote.lote_general_id else "—",
            lote.cajas_disp,
            float(lote.kilos_disp),
            float(lote.costo_por_kilo) if lote.costo_por_kilo is not None else None,
        ])
        fila = ws.max_row
        ws.cell(row=fila, column=8).number_format = "#,##0.00"
        ws.cell(row=fila, column=9).number_format = "#,##0.00"

    anchos = {"A": 20, "B": 20, "C": 12, "D": 14, "E": 16, "F": 16, "G": 16, "H": 16, "I": 12}
    for col, ancho in anchos.items():
        ws.column_dimensions[col].width = ancho
    ws.freeze_panes = "A2"
    return wb


def construir_libro_excel_producto(filas, proveedores):
    wb = Workbook()
    ws = wb.active
    ws.title = "Existencias por producto"
    encabezados = ["Talla", "Tipo"] + proveedores + ["Total cajas", "Total kilos"]
    ws.append(encabezados)
    _estilizar_encabezado(ws, len(encabezados))

    for fila in filas:
        total_cajas = sum(c['cajas'] for c in fila['por_proveedor'].values())
        total_kilos = sum((c['kilos'] for c in fila['por_proveedor'].values()), Decimal('0'))
        renglon = [fila['talla'], fila['tipo']]
        for proveedor in proveedores:
            celda = fila['por_proveedor'].get(proveedor)
            renglon.append(celda['cajas'] if celda else None)
        renglon += [total_cajas, float(total_kilos)]
        ws.append(renglon)

    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 14
    for i in range(len(proveedores) + 2):
        col_letra = ws.cell(row=1, column=3 + i).column_letter
        ws.column_dimensions[col_letra].width = 14
    ws.freeze_panes = "A2"
    return wb


def describir_filtros_texto(filtros_descripcion):
    return " · ".join(filtros_descripcion) if filtros_descripcion else "Todas las cámaras"


ESTILOS = getSampleStyleSheet()

_ESTILO_CELDA = ParagraphStyle(
    "celda-existencias", parent=ESTILOS["Normal"], fontSize=7.5, leading=9, alignment=TA_LEFT,
)
_ESTILO_CELDA_DERECHA = ParagraphStyle(
    "celda-existencias-derecha", parent=ESTILOS["Normal"], fontSize=7.5, leading=9, alignment=TA_RIGHT,
)
_ESTILO_CELDA_CENTRO = ParagraphStyle(
    "celda-existencias-centro", parent=ESTILOS["Normal"], fontSize=7.5, leading=9, alignment=TA_CENTER,
)


def _celda(texto, alineacion=TA_LEFT):
    estilo = {TA_LEFT: _ESTILO_CELDA, TA_RIGHT: _ESTILO_CELDA_DERECHA, TA_CENTER: _ESTILO_CELDA_CENTRO}[alineacion]
    return Paragraph(escape(str(texto)), estilo)


def _pie_pagina_pdf(canvas, documento):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#888888"))
    ancho_pagina, _ = documento.pagesize
    canvas.drawRightString(ancho_pagina - 1.2 * cm, 0.7 * cm, f"Página {documento.page}")
    canvas.restoreState()


def _encabezado_documento(titulo, filtros_descripcion):
    estilo_empresa = ParagraphStyle(
        "empresa", parent=ESTILOS["Heading1"], fontSize=15, textColor=COLOR_ENCABEZADO_RL, spaceAfter=1,
    )
    estilo_titulo = ParagraphStyle(
        "titulo", parent=ESTILOS["Normal"], fontSize=12, textColor=colors.black, spaceAfter=6,
    )
    estilo_subtitulo = ParagraphStyle(
        "subtitulo", parent=ESTILOS["Normal"], fontSize=9.5, textColor=colors.HexColor("#444444"), spaceAfter=2,
    )
    estilo_generado = ParagraphStyle(
        "generado", parent=ESTILOS["Normal"], fontSize=8, textColor=colors.HexColor("#999999"), spaceBefore=4,
    )
    elementos = [
        Paragraph("Importadora y Exportadora de Mariscos", estilo_empresa),
        Paragraph(titulo, estilo_titulo),
        Paragraph("Filtros: " + describir_filtros_texto(filtros_descripcion), estilo_subtitulo),
        Paragraph(
            f"Generado el {timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M')}",
            estilo_generado,
        ),
        Spacer(1, 0.5 * cm),
    ]
    return elementos


def _documento_base(titulo):
    buffer = BytesIO()
    documento = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        leftMargin=1.2 * cm,
        rightMargin=1.2 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.2 * cm,
        title=titulo,
    )
    return buffer, documento


def construir_pdf_existencias_lote(lotes, filtros_descripcion):
    lotes = list(lotes)
    buffer, documento = _documento_base("Reporte de existencias por lote")
    elementos = _encabezado_documento("Existencias por lote", filtros_descripcion)

    encabezados = ["Cámara", "Proveedor", "Talla", "Tipo", "Lote", "Recibo", "Cajas disp.", "Kilos disp.", "Costo/kg"]
    filas_tabla = [encabezados]
    for lote in lotes:
        filas_tabla.append([
            _celda(lote.camara.nombre if lote.camara_id else "Venta directa"),
            _celda(lote.proveedor_origen.nombre if lote.proveedor_origen_id else "—"),
            _celda(lote.producto.talla, TA_CENTER),
            _celda(lote.producto.tipo, TA_CENTER),
            _celda(lote.lote_proveedor),
            _celda(lote.lote_general.codigo if lote.lote_general_id else "—", TA_CENTER),
            _celda(f"{lote.cajas_disp:,}", TA_RIGHT),
            _celda(f"{lote.kilos_disp:,.2f}", TA_RIGHT),
            _celda(f"{lote.costo_por_kilo:,.2f}" if lote.costo_por_kilo is not None else "—", TA_RIGHT),
        ])

    if len(filas_tabla) == 1:
        elementos.append(Paragraph("No hay existencias para los filtros seleccionados.", ESTILOS["Normal"]))
    else:
        anchos = [3 * cm, 3.6 * cm, 2 * cm, 2.4 * cm, 3 * cm, 2.6 * cm, 2.6 * cm, 2.6 * cm, 2.2 * cm]
        tabla = Table(filas_tabla, colWidths=anchos, repeatRows=1)
        tabla.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), COLOR_ENCABEZADO_RL),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8.5),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F7F7")]),
        ]))
        elementos.append(tabla)

    documento.build(elementos, onFirstPage=_pie_pagina_pdf, onLaterPages=_pie_pagina_pdf)
    buffer.seek(0)
    return buffer


def construir_pdf_existencias_producto(filas, proveedores, filtros_descripcion):
    buffer, documento = _documento_base("Reporte de existencias por producto")
    elementos = _encabezado_documento("Existencias por producto", filtros_descripcion)

    encabezados = ["Talla", "Tipo"] + proveedores + ["Total"]
    filas_tabla = [[_celda(h, TA_CENTER) for h in encabezados]]
    for fila in filas:
        total_cajas = sum(c['cajas'] for c in fila['por_proveedor'].values())
        renglon = [_celda(fila['talla'], TA_CENTER), _celda(fila['tipo'], TA_CENTER)]
        for proveedor in proveedores:
            celda = fila['por_proveedor'].get(proveedor)
            renglon.append(_celda(f"{celda['cajas']:,}" if celda else "—", TA_RIGHT))
        renglon.append(_celda(f"{total_cajas:,}", TA_RIGHT))
        filas_tabla.append(renglon)

    if len(filas_tabla) == 1:
        elementos.append(Paragraph("No hay existencias para los filtros seleccionados.", ESTILOS["Normal"]))
    else:
        # Ancho disponible en landscape letter, menos márgenes (~24.7cm útiles).
        ancho_disponible = 24.7 * cm
        ancho_fijo = 4 * cm  # Talla + Tipo
        num_columnas_datos = len(proveedores) + 1  # + Total
        ancho_columna_dato = max((ancho_disponible - ancho_fijo) / num_columnas_datos, 1.6 * cm)
        anchos = [2 * cm, 2 * cm] + [ancho_columna_dato] * num_columnas_datos

        tabla = Table(filas_tabla, colWidths=anchos, repeatRows=1)
        tabla.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), COLOR_ENCABEZADO_RL),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F7F7")]),
            ("FONTNAME", (-1, 1), (-1, -1), "Helvetica-Bold"),
        ]))
        elementos.append(tabla)
        if len(proveedores) > 0 and 'Otros' in proveedores:
            elementos.append(Spacer(1, 0.4 * cm))
            elementos.append(Paragraph(
                "\"Otros\" agrupa a los proveedores con menor existencia para que la tabla quepa en la hoja. "
                "El detalle completo por proveedor está disponible en el Excel.",
                ParagraphStyle("nota", parent=ESTILOS["Normal"], fontSize=7.5, textColor=colors.HexColor("#888888")),
            ))

    documento.build(elementos, onFirstPage=_pie_pagina_pdf, onLaterPages=_pie_pagina_pdf)
    buffer.seek(0)
    return buffer
