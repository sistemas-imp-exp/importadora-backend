from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import ArqueoCaja
from .reportes_arqueo import calcular_resumen_arqueo, construir_libro_excel_arqueo, construir_pdf_arqueo, nombre_archivo_arqueo


def _obtener_arqueo(arqueo_id):
    return (
        ArqueoCaja.objects
        .select_related('corte', 'usuario')
        .prefetch_related('divisas__divisa', 'divisas__conteos__denominacion')
        .filter(pk=arqueo_id)
        .first()
    )


@api_view(["GET"])
def exportar_arqueo_excel(request, arqueo_id):
    arqueo = _obtener_arqueo(arqueo_id)
    if not arqueo:
        return Response({"detail": "No se encontró el arqueo."}, status=status.HTTP_404_NOT_FOUND)

    resumen = calcular_resumen_arqueo(arqueo)
    libro = construir_libro_excel_arqueo(arqueo, resumen)

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{nombre_archivo_arqueo(arqueo)}.xlsx"'
    libro.save(response)
    return response


@api_view(["GET"])
def exportar_arqueo_pdf(request, arqueo_id):
    arqueo = _obtener_arqueo(arqueo_id)
    if not arqueo:
        return Response({"detail": "No se encontró el arqueo."}, status=status.HTTP_404_NOT_FOUND)

    resumen = calcular_resumen_arqueo(arqueo)
    buffer = construir_pdf_arqueo(arqueo, resumen)

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{nombre_archivo_arqueo(arqueo)}.pdf"'
    return response
