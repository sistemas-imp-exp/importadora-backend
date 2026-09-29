from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from security.permissions import AreaInventario

from .models import Empresa
from .reporte_camaras import construir_excel, construir_pdf, construir_reporte, serializar_reporte


def _parametros(request):
    """(fecha, camara_id, empresa_id, subtítulo) o una Response 400 si la fecha no sirve."""
    params = request.query_params
    texto_fecha = (params.get("fecha") or "").strip()
    if texto_fecha:
        fecha = parse_date(texto_fecha)
        if fecha is None:
            return None, Response({"fecha": "Fecha inválida, usa AAAA-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        if fecha > timezone.localdate():
            return None, Response({"fecha": "La fecha no puede ser futura."}, status=status.HTTP_400_BAD_REQUEST)
    else:
        fecha = timezone.localdate()

    empresa_id = params.get("empresa") or None
    empresa = Empresa.objects.filter(pk=empresa_id).first() if empresa_id else None
    return (fecha, params.get("camara") or None, empresa_id, empresa.nombre if empresa else ""), None


def _nombre_archivo(fecha, extension):
    return f'attachment; filename="existencias-por-camara-{fecha:%Y-%m-%d}.{extension}"'


@api_view(["GET"])
@permission_classes([AreaInventario])
def reporte_existencias_camara(request):
    parametros, error = _parametros(request)
    if error:
        return error
    fecha, camara_id, empresa_id, _ = parametros
    reporte = construir_reporte(fecha, camara_id, empresa_id)
    return Response({"fecha": fecha.isoformat(), "camaras": serializar_reporte(reporte)})


@api_view(["GET"])
@permission_classes([AreaInventario])
def reporte_existencias_camara_pdf(request):
    parametros, error = _parametros(request)
    if error:
        return error
    fecha, camara_id, empresa_id, subtitulo = parametros
    buffer = construir_pdf(construir_reporte(fecha, camara_id, empresa_id), fecha, subtitulo)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = _nombre_archivo(fecha, "pdf")
    return response


@api_view(["GET"])
@permission_classes([AreaInventario])
def reporte_existencias_camara_excel(request):
    parametros, error = _parametros(request)
    if error:
        return error
    fecha, camara_id, empresa_id, subtitulo = parametros
    buffer = construir_excel(construir_reporte(fecha, camara_id, empresa_id), fecha, subtitulo)
    response = HttpResponse(
        buffer.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = _nombre_archivo(fecha, "xlsx")
    return response
