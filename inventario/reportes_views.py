from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes

from security.permissions import AreaInventario

from .reportes import (
    MAX_COLUMNAS_PROVEEDOR_PDF,
    construir_libro_excel_lote,
    construir_libro_excel_producto,
    construir_pdf_existencias_lote,
    construir_pdf_existencias_producto,
    construir_pivote,
    describir_filtros,
    obtener_lotes_filtrados,
)


@api_view(["GET"])
@permission_classes([AreaInventario])
def exportar_existencias_excel(request):
    lotes = obtener_lotes_filtrados(request.query_params)
    modo = request.query_params.get("modo", "producto")

    if modo == "lote":
        libro = construir_libro_excel_lote(lotes)
    else:
        filas, proveedores = construir_pivote(lotes)
        libro = construir_libro_excel_producto(filas, proveedores)

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="existencias.xlsx"'
    libro.save(response)
    return response


@api_view(["GET"])
@permission_classes([AreaInventario])
def exportar_existencias_pdf(request):
    lotes = obtener_lotes_filtrados(request.query_params)
    modo = request.query_params.get("modo", "producto")
    filtros_descripcion = describir_filtros(request.query_params)

    if modo == "lote":
        buffer = construir_pdf_existencias_lote(lotes, filtros_descripcion)
    else:
        filas, proveedores = construir_pivote(lotes, max_columnas=MAX_COLUMNAS_PROVEEDOR_PDF)
        buffer = construir_pdf_existencias_producto(filas, proveedores, filtros_descripcion)

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="existencias.pdf"'
    return response
