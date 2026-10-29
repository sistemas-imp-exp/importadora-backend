"""
Saldo de cartera: saldo inicial + ventas (salidas de Inventario, ver ventas.py)
+ cargos − pagos − abonos, sin lo cancelado.
Se calcula siempre (no hay saldos guardados que mantener), con una consulta
agregada por tipo de movimiento para todos los clientes a la vez.
"""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Max, Sum

from .models import AjusteCliente, PagoCliente, SaldoInicialCliente

CERO = Decimal('0')


def _filtrar(queryset, clientes=None, hasta=None, campo_fecha='fecha'):
    if clientes is not None:
        queryset = queryset.filter(cliente_id__in=clientes)
    if hasta is not None:
        queryset = queryset.filter(**{f'{campo_fecha}__lte': hasta})
    return queryset


def saldos_por_cliente(clientes=None, hasta=None):
    """{cliente_id: {empresa_id: saldo}} con los movimientos hasta `hasta` (incluido)."""
    saldos = defaultdict(lambda: defaultdict(lambda: CERO))

    for fila in _filtrar(SaldoInicialCliente.objects.all(), clientes, hasta).values('cliente_id', 'empresa_id', 'monto'):
        saldos[fila['cliente_id']][fila['empresa_id']] += fila['monto']

    # Los pagos por identificar (sin cliente) no mueven ningún saldo.
    pagos = _filtrar(PagoCliente.objects.filter(cancelado=False, cliente__isnull=False), clientes, hasta)
    for fila in pagos.values('cliente_id', 'empresa_id').annotate(total=Sum('monto')):
        saldos[fila['cliente_id']][fila['empresa_id']] -= fila['total']

    # Import diferido: ventas.py usa fecha_corte() de este módulo.
    from .ventas import importe, lineas_venta
    for linea in lineas_venta(clientes=clientes, hasta=hasta).select_related(None).select_related(
        'salida', 'entrada_detalle__entrada',
    ):
        monto = importe(linea)
        if monto is not None:
            # Una venta en resguardo no tiene lote: la empresa es la de la venta.
            empresa_id = linea.entrada_detalle.entrada.empresa_id if linea.entrada_detalle_id else linea.salida.empresa_id
            saldos[linea.salida.cliente_id][empresa_id] += monto

    ajustes = _filtrar(AjusteCliente.objects.filter(cancelado=False), clientes, hasta)
    for fila in ajustes.values('cliente_id', 'empresa_id', 'naturaleza').annotate(total=Sum('monto')):
        signo = 1 if fila['naturaleza'] == AjusteCliente.CARGO else -1
        saldos[fila['cliente_id']][fila['empresa_id']] += signo * fila['total']

    return saldos


def ultimos_pagos(clientes=None):
    """
    {cliente_id: (fecha, monto)} del último pago activo capturado en el
    sistema (si hay varios ese día, el último capturado).
    """
    pagos = PagoCliente.objects.filter(cancelado=False, cliente__isnull=False)
    if clientes is not None:
        pagos = pagos.filter(cliente_id__in=clientes)
    ultimos = {}
    for pago in pagos.order_by('cliente_id', '-fecha', '-creado').values('cliente_id', 'fecha', 'monto'):
        ultimos.setdefault(pago['cliente_id'], (pago['fecha'], pago['monto']))
    return ultimos


def por_identificar(hasta=None):
    """{empresa_id: (cantidad, monto)} de los pagos activos que aún no tienen cliente."""
    pagos = PagoCliente.objects.filter(cancelado=False, cliente__isnull=True)
    if hasta is not None:
        pagos = pagos.filter(fecha__lte=hasta)
    from django.db.models import Count
    return {
        f['empresa_id']: (f['cantidad'], f['total'])
        for f in pagos.values('empresa_id').annotate(cantidad=Count('id'), total=Sum('monto'))
    }


def fecha_corte():
    """Fecha de la carga inicial (la más reciente si hubiera varias)."""
    return SaldoInicialCliente.objects.aggregate(fecha=Max('fecha'))['fecha']
