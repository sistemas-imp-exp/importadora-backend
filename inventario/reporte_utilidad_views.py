from django.http import HttpResponse
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from security.permissions import AreaInventario

from .models import Camara, Cliente, Empresa, Producto, Proveedor
from .reporte_utilidad import AGRUPACIONES, construir_excel, construir_pdf, construir_reporte, serializar_reporte

FILTROS_CATALOGO = {
    "empresa": (Empresa, "Empresa", lambda o: o.nombre),
    "cliente": (Cliente, "Cliente", lambda o: o.nombre),
    "proveedor": (Proveedor, "Proveedor", lambda o: o.nombre),
    "producto": (Producto, "Producto", lambda o: f"{o.talla} {o.tipo}"),
    "camara": (Camara, "Cámara", lambda o: o.nombre),
}


def _parametros(request):
    """(filtros, subtítulo legible) o una Response 400."""
    params = request.query_params
    fechas = {}
    for nombre in ("desde", "hasta"):
        texto = (params.get(nombre) or "").strip()
        fecha = parse_date(texto) if texto else None
        if fecha is None:
            return None, Response({nombre: "Indica la fecha en formato AAAA-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        fechas[nombre] = fecha
    if fechas["desde"] > fechas["hasta"]:
        return None, Response({"desde": "La fecha inicial no puede ser posterior a la final."},
                              status=status.HTTP_400_BAD_REQUEST)

    filtros = {**fechas, "agrupar": params.get("agrupar") if params.get("agrupar") in AGRUPACIONES else "producto"}
    partes = [f"{fechas['desde']:%d/%m/%Y} al {fechas['hasta']:%d/%m/%Y}", f"por {AGRUPACIONES[filtros['agrupar']].lower()}"]
    for clave, (modelo, etiqueta, nombre) in FILTROS_CATALOGO.items():
        valor = (params.get(clave) or "").strip()
        if not valor:
            continue
        if not valor.isdigit():
            return None, Response({clave: "Valor inválido."}, status=status.HTTP_400_BAD_REQUEST)
        filtros[clave] = int(valor)
        objeto = modelo.objects.filter(pk=valor).first()
        if objeto:
            partes.append(f"{etiqueta}: {nombre(objeto)}")
    return (filtros, " · ".join(partes)), None


def _nombre_archivo(filtros, extension):
    return f'attachment; filename="utilidad-{filtros["desde"]:%Y-%m-%d}-a-{filtros["hasta"]:%Y-%m-%d}.{extension}"'


@api_view(["GET"])
@permission_classes([AreaInventario])
def reporte_utilidad(request):
    parametros, error = _parametros(request)
    if error:
        return error
    filtros, _ = parametros
    return Response(serializar_reporte(construir_reporte(filtros)))


@api_view(["GET"])
@permission_classes([AreaInventario])
def reporte_utilidad_excel(request):
    parametros, error = _parametros(request)
    if error:
        return error
    filtros, subtitulo = parametros
    buffer = construir_excel(construir_reporte(filtros), subtitulo)
    response = HttpResponse(
        buffer.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = _nombre_archivo(filtros, "xlsx")
    return response


@api_view(["GET"])
@permission_classes([AreaInventario])
def reporte_utilidad_pdf(request):
    parametros, error = _parametros(request)
    if error:
        return error
    filtros, subtitulo = parametros
    buffer = construir_pdf(construir_reporte(filtros), subtitulo)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = _nombre_archivo(filtros, "pdf")
    return response
