"""
Saldos de caja calculados por fecha.

No hay saldos guardados ni aperturas: el saldo de un día es el saldo inicial
de la divisa (SaldoInicial, sin fecha, lo fija el superusuario) más todos los
ingresos y menos todos los egresos no cancelados hasta ese día, desde el primer
movimiento registrado. Un ajuste por conteo físico se captura como un movimiento más. Un movimiento con fecha atrasada corrige solo
todos los días siguientes, sin recalcular nada guardado.

Cada función resuelve los saldos acumulados con una consulta agregada por
divisa (índice por fecha), sin importar cuánta historia haya.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Case, Count, DecimalField, F, Sum, When

from .models import Divisa, MovimientoDivisa, MovimientoTesoreria, SaldoInicial

CERO = Decimal("0")


def _no_cancelados():
    return MovimientoDivisa.objects.filter(movimiento__cancelado=False)


def _flujos(desde, hasta):
    """{fecha: {divisa_id: [ingresos, egresos]}} de movimientos no cancelados entre ambas fechas (inclusive)."""
    filas = (
        _no_cancelados()
        .filter(movimiento__fecha__gte=desde, movimiento__fecha__lte=hasta)
        .values("movimiento__fecha", "divisa_id", "movimiento__tipo")
        .annotate(total=Sum("cantidad"))
    )
    flujos = defaultdict(lambda: defaultdict(lambda: [CERO, CERO]))
    for fila in filas:
        indice = 0 if fila["movimiento__tipo"] == MovimientoTesoreria.INGRESO else 1
        flujos[fila["movimiento__fecha"]][fila["divisa_id"]][indice] += fila["total"]
    return flujos


def saldos_al(fecha):
    """Saldo por divisa al CIERRE de `fecha`: saldo inicial + ingresos − egresos de toda la historia hasta ese día."""
    filas = (
        _no_cancelados()
        .filter(movimiento__fecha__lte=fecha)
        .values("divisa_id")
        .annotate(saldo=Sum(Case(
            When(movimiento__tipo=MovimientoTesoreria.INGRESO, then=F("cantidad")),
            default=-F("cantidad"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        )))
    )
    saldos = defaultdict(lambda: CERO, SaldoInicial.objects.values_list("divisa_id", "monto"))
    for fila in filas:
        saldos[fila["divisa_id"]] += fila["saldo"]
    return dict(saldos)


def _divisas(ids):
    """Divisas a mostrar: las activas más cualquier otra con saldo o movimientos."""
    return list(Divisa.objects.filter(activa=True) | Divisa.objects.filter(id__in=ids))


def resumen_dia(fecha):
    """Por divisa: saldo al inicio del día, ingresos, egresos y saldo al cierre."""
    inicial = saldos_al(fecha - timedelta(days=1))
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
    return filas


def historial(desde, hasta):
    """
    Días con movimientos entre `desde` y `hasta`, con el saldo al cierre por
    divisa y si alguno quedó en negativo. Una consulta para el saldo previo a
    `desde`, otra para los flujos del rango, y los saldos se acumulan día por día.
    """
    saldos = defaultdict(lambda: CERO, saldos_al(desde - timedelta(days=1)))
    flujos = _flujos(desde, hasta)
    conteos = dict(
        MovimientoTesoreria.objects.filter(fecha__gte=desde, fecha__lte=hasta)
        .values_list("fecha").annotate(n=Count("id")).values_list("fecha", "n")
    )

    dias = []
    for dia in sorted(set(flujos) | set(conteos)):
        movimientos_dia = flujos.get(dia, {})
        for divisa_id, (ingresos, egresos) in movimientos_dia.items():
            saldos[divisa_id] += ingresos - egresos
        dias.append({
            "fecha": dia,
            "movimientos": conteos.get(dia, 0),
            "ingresos": {d: v[0] for d, v in movimientos_dia.items()},
            "egresos": {d: v[1] for d, v in movimientos_dia.items()},
            "saldos": dict(saldos),
            "negativo": any(v < 0 for v in saldos.values()),
        })
    return dias
