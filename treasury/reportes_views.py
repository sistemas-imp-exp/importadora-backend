from decimal import Decimal

from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .reportes import (
    PREVIEW_LIMIT,
    calcular_resumen_por_divisa,
    construir_libro_excel,
    construir_pdf_movimientos,
    describir_filtros,
    obtener_lineas_filtradas,
)

DOS_DECIMALES = Decimal("0.01")


def _dinero(valor):
    # SQLite no siempre conserva la escala fija al hacer SUM(); se normaliza
    # aquí para que la API entregue siempre 2 decimales, sin importar motor.
    return valor.quantize(DOS_DECIMALES)


def _serializar_linea(linea):
    m = linea.movimiento
    return {
        "id": linea.id,
        "movimiento_id": m.id,
        "folio": m.folio,
        "fecha": m.fecha.isoformat(),
        "corte": m.corte_id,
        "tipo": m.tipo,
        "autorizo": m.autorizo,
        "beneficiario": m.beneficiario,
        "concepto": m.concepto,
        "divisa": {
            "id": linea.divisa_id,
            "codigo": linea.divisa.codigo,
            "simbolo": linea.divisa.simbolo,
        },
        "cantidad": str(linea.cantidad),
        "cancelado": m.cancelado,
        "editado": m.editado,
    }


def _serializar_resumen(filas):
    resultado = []
    for fila in filas:
        ingresos = _dinero(fila["ingresos"])
        egresos = _dinero(fila["egresos"])
        resultado.append({
            "divisa": {
                "id": fila["divisa_id"],
                "codigo": fila["divisa__codigo"],
                "nombre": fila["divisa__nombre"],
                "simbolo": fila["divisa__simbolo"],
            },
            "ingresos": str(ingresos),
            "egresos": str(egresos),
            "neto": str(ingresos - egresos),
            "movimientos_activos": fila["movimientos_activos"],
            "movimientos_cancelados": fila["movimientos_cancelados"],
        })
    return resultado


@api_view(["GET"])
def resumen_movimientos(request):
    try:
        lineas = obtener_lineas_filtradas(request.query_params)
    except ValidationError as exc:
        return Response({"detail": exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST)

    total = lineas.count()
    resumen = calcular_resumen_por_divisa(lineas)
    muestra = [_serializar_linea(linea) for linea in lineas[:PREVIEW_LIMIT]]

    return Response({
        "total": total,
        "muestra": muestra,
        "muestra_limitada": total > PREVIEW_LIMIT,
        "resumen": _serializar_resumen(resumen),
    })


@api_view(["GET"])
def exportar_movimientos_excel(request):
    try:
        lineas = obtener_lineas_filtradas(request.query_params)
    except ValidationError as exc:
        return Response({"detail": exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST)

    resumen = calcular_resumen_por_divisa(lineas)
    libro = construir_libro_excel(lineas, resumen)

    fecha_inicio = request.query_params.get("fecha_inicio")
    fecha_fin = request.query_params.get("fecha_fin")
    nombre_archivo = f"reporte_movimientos_{fecha_inicio}_{fecha_fin}.xlsx"

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{nombre_archivo}"'
    libro.save(response)
    return response


@api_view(["GET"])
def exportar_movimientos_pdf(request):
    try:
        lineas = obtener_lineas_filtradas(request.query_params)
    except ValidationError as exc:
        return Response({"detail": exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST)

    resumen = calcular_resumen_por_divisa(lineas)
    fecha_inicio = parse_date(request.query_params.get("fecha_inicio"))
    fecha_fin = parse_date(request.query_params.get("fecha_fin"))
    filtros_descripcion = describir_filtros(request.query_params)

    buffer = construir_pdf_movimientos(lineas, resumen, fecha_inicio, fecha_fin, filtros_descripcion)

    nombre_archivo = f"reporte_movimientos_{fecha_inicio}_{fecha_fin}.pdf"
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{nombre_archivo}"'
    return response
