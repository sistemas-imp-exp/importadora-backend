"""
Avisos automáticos entre Cartera e Inventario por las ventas verificadas:
- Cartera reporta una diferencia → aviso a Inventario para que corrija la salida.
- Inventario corrige o elimina una salida que Cartera ya revisó → aviso a Cartera.
"""
from collections import defaultdict

from notificaciones.servicio import notificar
from security.permissions import AREA_CARTERA, AREA_INVENTARIO

from .models import VerificacionVenta
from .ventas import huella


def avisar_diferencias(lineas, comentario, usuario):
    por_salida = defaultdict(list)
    for linea in lineas:
        por_salida[linea.salida].append(linea)
    for salida, de_la_salida in por_salida.items():
        notificar(
            AREA_CARTERA, AREA_INVENTARIO, 'venta_diferencia',
            f'Diferencia en la salida {salida.referencia}',
            f'{salida.cliente.nombre} · {len(de_la_salida)} línea(s) no coinciden con la nota.\n{comentario}'.strip(),
            f'/inventario/salidas?busqueda={salida.clave_busqueda}',
            usuario,
        )


def revisiones_de(salida):
    """{linea_id: (huella revisada, estado)} de las líneas que Cartera ya revisó."""
    return {
        v.salida_detalle_id: (v.huella, v.estado)
        for v in VerificacionVenta.objects.filter(salida_detalle__salida=salida)
    }


def avisar_correccion(salida, revisiones, usuario, eliminada=False):
    """Compara contra las revisiones tomadas antes del cambio y avisa a Cartera si alguna dejó de valer."""
    if not revisiones or salida.cliente_id is None:
        return
    if eliminada:
        cambiadas = list(revisiones.values())
    else:
        actuales = {linea.pk: linea for linea in salida.detalles.select_related('salida')}
        cambiadas = [
            (h, estado) for linea_id, (h, estado) in revisiones.items()
            if linea_id not in actuales or huella(actuales[linea_id]) != h
        ]
    if not cambiadas:
        return
    corregidas = sum(1 for _, estado in cambiadas if estado == VerificacionVenta.DIFERENCIA)
    detalle = f'{salida.cliente.nombre} · {len(cambiadas)} línea(s) revisada(s) cambiaron'
    if corregidas:
        detalle += f' ({corregidas} con diferencia reportada)'
    detalle += '. Vuelven a pendientes de verificar.' if not eliminada else '. La salida se eliminó.'
    accion = 'eliminó' if eliminada else 'corrigió'
    notificar(
        AREA_INVENTARIO, AREA_CARTERA, 'salida_corregida',
        f'Inventario {accion} la salida {salida.referencia}',
        detalle,
        f'/cartera/clientes/{salida.cliente_id}',
        usuario,
    )
