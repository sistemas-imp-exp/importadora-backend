"""
Saldos de caja calculados por fecha.

No hay saldos guardados: el saldo de un día sale de la apertura vigente
(AperturaPeriodo más reciente con fecha <= ese día) más los ingresos y menos
los egresos no cancelados desde la fecha de la apertura hasta ese día. Por eso
un movimiento capturado con fecha atrasada corrige solo todos los días
siguientes, sin recalcular nada guardado.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum

from .models import AperturaPeriodo, Divisa, MovimientoDivisa, MovimientoTesoreria

CERO = Decimal("0")


def _flujos(desde, hasta):
    """{fecha: {divisa_id: [ingresos, egresos]}} de movimientos no cancelados entre ambas fechas (inclusive)."""
    filas = (
        MovimientoDivisa.objects
        .filter(movimiento__cancelado=False, movimiento__fecha__gte=desde, movimiento__fecha__lte=hasta)
        .values("movimiento__fecha", "divisa_id", "movimiento__tipo")
        .annotate(total=Sum("cantidad"))
    )
    flujos = defaultdict(lambda: defaultdict(lambda: [CERO, CERO]))
    for fila in filas:
        indice = 0 if fila["movimiento__tipo"] == MovimientoTesoreria.INGRESO else 1
        flujos[fila["movimiento__fecha"]][fila["divisa_id"]][indice] += fila["total"]
    return flujos


def _montos(apertura):
    return {s.divisa_id: s.monto for s in apertura.saldos.all()}


def saldos_al(fecha):
    """Saldo por divisa al CIERRE de `fecha` ({} si aún no hay apertura)."""
    apertura = AperturaPeriodo.vigente(fecha)
    if apertura is None:
        return {}
    saldos = defaultdict(lambda: CERO, _montos(apertura))
    for por_divisa in _flujos(apertura.fecha, fecha).values():
        for divisa_id, (ingresos, egresos) in por_divisa.items():
            saldos[divisa_id] += ingresos - egresos
    return dict(saldos)


def _divisas(ids):
    """Divisas a mostrar: las activas más cualquier otra con saldo o movimientos."""
    return list(Divisa.objects.filter(activa=True) | Divisa.objects.filter(id__in=ids))


def resumen_dia(fecha):
    """
    Por divisa: saldo al inicio del día, ingresos, egresos y saldo al cierre.
    Devuelve (apertura_vigente, filas); filas vacía si no hay apertura.
    """
    apertura = AperturaPeriodo.vigente(fecha)
    if apertura is None:
        return None, []
    # Una apertura con esta misma fecha ES el saldo al inicio del día.
    inicial = _montos(apertura) if apertura.fecha == fecha else saldos_al(fecha - timedelta(days=1))
    del_dia = _flujos(fecha, fecha).get(fecha, {})

    filas = []
    for divisa in sorted(_divisas(set(inicial) | set(del_dia)), key=lambda d: d.codigo):
        ingresos, egresos = del_dia.get(divisa.id, [CERO, CERO])
        saldo_inicial = inicial.get(divisa.id, CERO)
        final = saldo_inicial + ingresos - egresos
        filas.append({
            "divisa": divisa,
            "saldo_inicial": saldo_inicial,
            "ingresos": ingresos,
            "egresos": egresos,
            "saldo_final": final,
            "negativo": final < 0,
        })
    return apertura, filas


def historial(desde, hasta):
    """
    Días con actividad (movimientos o apertura) entre `desde` y `hasta`, con el
    saldo al cierre por divisa y si alguno quedó en negativo. Una sola consulta
    de flujos y los saldos se van acumulando día por día.
    """
    primera = AperturaPeriodo.primera()
    if primera is None or hasta < primera.fecha:
        return []
    desde = max(desde, primera.fecha)

    aperturas = {a.fecha: a for a in AperturaPeriodo.objects.filter(fecha__gte=desde, fecha__lte=hasta).prefetch_related("saldos")}
    flujos = _flujos(desde, hasta)
    conteos = dict(
        MovimientoTesoreria.objects.filter(fecha__gte=desde, fecha__lte=hasta)
        .values_list("fecha").annotate(n=Count("id")).values_list("fecha", "n")
    )
    # Saldo al inicio de `desde`.
    if desde in aperturas:
        saldos = defaultdict(lambda: CERO)
    else:
        saldos = defaultdict(lambda: CERO, saldos_al(desde - timedelta(days=1)))

    dias = []
    dia = desde
    while dia <= hasta:
        movimientos_dia = flujos.get(dia, {})
        if dia in aperturas:
            saldos = defaultdict(lambda: CERO, _montos(aperturas[dia]))
        for divisa_id, (ingresos, egresos) in movimientos_dia.items():
            saldos[divisa_id] += ingresos - egresos
        if movimientos_dia or dia in aperturas or conteos.get(dia):
            dias.append({
                "fecha": dia,
                "apertura": dia in aperturas,
                "movimientos": conteos.get(dia, 0),
                "ingresos": {d: v[0] for d, v in movimientos_dia.items()},
                "egresos": {d: v[1] for d, v in movimientos_dia.items()},
                "saldos": dict(saldos),
                "negativo": any(v < 0 for v in saldos.values()),
            })
        dia += timedelta(days=1)
    return dias
