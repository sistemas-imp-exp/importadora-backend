from decimal import Decimal

from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .reportes_nomina import (
    PREVIEW_LIMIT,
    calcular_resumen_por_banco,
    calcular_resumen_por_rancho,
    construir_libro_excel_nomina,
    construir_pdf_nomina,
    describir_filtros_nomina,
    obtener_detalles_filtrados,
)

DOS_DECIMALES = Decimal("0.01")


def _dinero(valor):
    return (valor or Decimal("0")).quantize(DOS_DECIMALES)


def _serializar_detalle(detalle):
    return {
        "id": detalle.id,
        "nomina_id": detalle.nomina_id,
        "semana": {
            "fecha_inicio": detalle.nomina.fecha_inicio.isoformat(),
            "fecha_fin": detalle.nomina.fecha_fin.isoformat(),
        },
        "rancho": {
            "id": detalle.empleado.rancho_id,
            "nombre": detalle.empleado.rancho.nombre,
        },
        "banco": {
            "id": detalle.empleado.banco_id,
            "nombre": detalle.empleado.banco.nombre,
        } if detalle.empleado.banco_id else None,
        "empleado": {
            "id": detalle.empleado_id,
            "nombre": detalle.empleado.nombre,
            "puesto": detalle.empleado.puesto.nombre,
        },
        "numero_cuenta": detalle.empleado.numero_cuenta,
        "nombre_cuenta": detalle.empleado.nombre_cuenta,
        "dias_trabajados": str(detalle.dias_trabajados),
        "salario_diario": str(detalle.salario_diario),
        "descuento": str(detalle.descuento),
        "total_bruto": str(_dinero(detalle.total_bruto)),
        "total_neto": str(_dinero(detalle.total_neto)),
    }


def _serializar_resumen(filas):
    resultado = []
    for fila in filas:
        total_bruto = _dinero(fila["total_bruto"])
        total_descuento = _dinero(fila["total_descuento"])
        resultado.append({
            "rancho": {
                "id": fila["empleado__rancho_id"],
                "nombre": fila["empleado__rancho__nombre"],
            },
            "empleados": fila["empleados"],
            "total_bruto": str(total_bruto),
            "total_descuento": str(total_descuento),
            "total_neto": str(total_bruto - total_descuento),
        })
    return resultado


def _serializar_resumen_banco(filas):
    resultado = []
    for fila in filas:
        total_bruto = _dinero(fila["total_bruto"])
        total_descuento = _dinero(fila["total_descuento"])
        resultado.append({
            "banco": {
                "id": fila["empleado__banco_id"],
                "nombre": fila["empleado__banco__nombre"] or "Sin banco",
            } if fila["empleado__banco_id"] else None,
            "empleados": fila["empleados"],
            "total_bruto": str(total_bruto),
            "total_descuento": str(total_descuento),
            "total_neto": str(total_bruto - total_descuento),
        })
    return resultado


@api_view(["GET"])
def resumen_nomina(request):
    try:
        detalles = obtener_detalles_filtrados(request.query_params)
    except ValidationError as exc:
        return Response({"detail": exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST)

    total = detalles.count()
    resumen = calcular_resumen_por_rancho(detalles)
    resumen_banco = calcular_resumen_por_banco(detalles)
    muestra = [_serializar_detalle(detalle) for detalle in detalles[:PREVIEW_LIMIT]]

    return Response({
        "total": total,
        "muestra": muestra,
        "muestra_limitada": total > PREVIEW_LIMIT,
        "resumen": _serializar_resumen(resumen),
        "resumen_banco": _serializar_resumen_banco(resumen_banco),
    })


@api_view(["GET"])
def exportar_nomina_excel(request):
    try:
        detalles = obtener_detalles_filtrados(request.query_params)
    except ValidationError as exc:
        return Response({"detail": exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST)

    resumen = calcular_resumen_por_rancho(detalles)
    resumen_banco = calcular_resumen_por_banco(detalles)
    libro = construir_libro_excel_nomina(detalles, resumen, resumen_banco)

    fecha_inicio = request.query_params.get("fecha_inicio")
    fecha_fin = request.query_params.get("fecha_fin")
    nombre_archivo = f"reporte_nomina_{fecha_inicio}_{fecha_fin}.xlsx"

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{nombre_archivo}"'
    libro.save(response)
    return response


@api_view(["GET"])
def exportar_nomina_pdf(request):
    try:
        detalles = obtener_detalles_filtrados(request.query_params)
    except ValidationError as exc:
        return Response({"detail": exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST)

    resumen = calcular_resumen_por_rancho(detalles)
    resumen_banco = calcular_resumen_por_banco(detalles)
    fecha_inicio = parse_date(request.query_params.get("fecha_inicio"))
    fecha_fin = parse_date(request.query_params.get("fecha_fin"))
    filtros_descripcion = describir_filtros_nomina(request.query_params)

    buffer = construir_pdf_nomina(detalles, resumen, resumen_banco, fecha_inicio, fecha_fin, filtros_descripcion)

    nombre_archivo = f"reporte_nomina_{fecha_inicio}_{fecha_fin}.pdf"
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{nombre_archivo}"'
    return response
