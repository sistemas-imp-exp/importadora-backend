from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from security.permissions import AreaInventario

from .models import Empresa, Producto, Proveedor
from .reporte_camaras import (
    SIN_PRESENTACION,
    construir_excel,
    construir_pdf,
    construir_reporte,
    serializar_reporte,
)

PRESENTACIONES = {**dict(Producto.PRESENTACION_CHOICES), SIN_PRESENTACION: "Sin presentación"}


def _filtros(params):
    """
    Filtros de producto y proveedor, repetibles (?talla=30-40&talla=40-50), y
    su descripción para el subtítulo de los archivos. Una Response 400 si
    alguno no sirve.
    """
    filtros = {campo: [v.strip() for v in params.getlist(campo) if v.strip()]
               for campo in ("presentacion", "talla", "tipo", "categoria", "proveedor")}
    invalidas = [v for v in filtros["presentacion"] if v not in PRESENTACIONES]
    if invalidas:
        return None, "", Response(
            {"presentacion": f"Presentación inválida: {', '.join(invalidas)}."}, status=status.HTTP_400_BAD_REQUEST,
        )
    if not all(v.isdigit() for v in filtros["proveedor"]):
        return None, "", Response({"proveedor": "Proveedor inválido."}, status=status.HTTP_400_BAD_REQUEST)

    partes = []
    if filtros["presentacion"]:
        partes.append(", ".join(PRESENTACIONES[v] for v in filtros["presentacion"]))
    for campo, etiqueta in (("talla", "Talla"), ("tipo", "Tipo"), ("categoria", "Categoría")):
        if filtros[campo]:
            partes.append(f"{etiqueta}: {', '.join(filtros[campo])}")
    if filtros["proveedor"]:
        nombres = Proveedor.objects.filter(pk__in=filtros["proveedor"]).order_by("nombre").values_list("nombre", flat=True)
        partes.append(f"Proveedor: {', '.join(nombres)}")
    return filtros, " · ".join(partes), None


def _parametros(request):
    """(fecha, camara_id, empresa_id, filtros, subtítulo) o una Response 400 si algo no sirve."""
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

    filtros, descripcion, error = _filtros(params)
    if error:
        return None, error
    empresa_id = params.get("empresa") or None
    empresa = Empresa.objects.filter(pk=empresa_id).first() if empresa_id else None
    subtitulo = " · ".join(filter(None, [empresa.nombre if empresa else "", descripcion]))
    return (fecha, params.get("camara") or None, empresa_id, filtros, subtitulo), None


def _nombre_archivo(fecha, extension):
    return f'attachment; filename="existencias-por-camara-{fecha:%Y-%m-%d}.{extension}"'


@api_view(["GET"])
@permission_classes([AreaInventario])
def reporte_existencias_camara(request):
    parametros, error = _parametros(request)
    if error:
        return error
    fecha, camara_id, empresa_id, filtros, _ = parametros
    reporte = construir_reporte(fecha, camara_id, empresa_id, filtros)
    return Response({"fecha": fecha.isoformat(), "camaras": serializar_reporte(reporte)})


@api_view(["GET"])
@permission_classes([AreaInventario])
def reporte_existencias_camara_pdf(request):
    parametros, error = _parametros(request)
    if error:
        return error
    fecha, camara_id, empresa_id, filtros, subtitulo = parametros
    buffer = construir_pdf(construir_reporte(fecha, camara_id, empresa_id, filtros), fecha, subtitulo)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = _nombre_archivo(fecha, "pdf")
    return response


@api_view(["GET"])
@permission_classes([AreaInventario])
def reporte_existencias_camara_excel(request):
    parametros, error = _parametros(request)
    if error:
        return error
    fecha, camara_id, empresa_id, filtros, subtitulo = parametros
    buffer = construir_excel(construir_reporte(fecha, camara_id, empresa_id, filtros), fecha, subtitulo)
    response = HttpResponse(
        buffer.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = _nombre_archivo(fecha, "xlsx")
    return response
