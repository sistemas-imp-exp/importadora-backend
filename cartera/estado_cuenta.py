"""
Datos de los reportes de Cartera (los usan la API y los PDF/Excel):
  - estado de cuenta de un cliente: saldo inicial, pagos y ajustes en orden,
    con el saldo corrido (lo cancelado se muestra pero no lo mueve);
  - saldos de clientes: por empresa y conjunto, con el último pago.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from inventario.models import Cliente, Empresa

from .models import AjusteCliente, ClienteCartera, PagoCliente, SaldoInicialCliente
from .ventas import empresa_de, estado, importe, lineas_venta, producto_de
from .saldos import saldos_por_cliente, ultimos_pagos

CERO = Decimal('0')


@dataclass
class Renglon:
    fecha: date
    tipo: str  # 'inicial' | 'venta' | 'pago' | 'cargo' | 'abono'
    empresa: str
    referencia: str
    concepto: str
    cargo: Decimal
    abono: Decimal
    cancelado: bool
    motivo_cancelacion: str
    id: int | None
    orden: tuple
    saldo: Decimal | None = None
    # Ventas: una entrada por línea de la salida (producto, kilos, precio, importe, verificación).
    detalle: list = field(default_factory=list)


def estado_de_cuenta(cliente, empresa=None):
    """Renglones del cliente en orden cronológico, con el saldo corrido."""
    renglones = []

    iniciales = SaldoInicialCliente.objects.filter(cliente=cliente).select_related('empresa')
    pagos = PagoCliente.objects.filter(cliente=cliente).select_related('empresa', 'ingreso_tesoreria')
    ajustes = AjusteCliente.objects.filter(cliente=cliente).select_related('empresa', 'contraparte__empresa')
    if empresa is not None:
        iniciales, pagos, ajustes = (q.filter(empresa=empresa) for q in (iniciales, pagos, ajustes))

    for inicial in iniciales:
        renglones.append(Renglon(
            fecha=inicial.fecha, tipo='inicial', empresa=inicial.empresa.nombre, referencia='',
            concepto=f'Saldo al {inicial.fecha:%d/%m/%Y}',
            cargo=max(inicial.monto, CERO), abono=max(-inicial.monto, CERO),
            cancelado=False, motivo_cancelacion='', id=inicial.id, orden=(inicial.fecha, 0, inicial.id),
        ))
    for pago in pagos:
        referencia = pago.referencia
        if pago.ingreso_tesoreria_id:
            referencia = f'Tesorería folio {pago.ingreso_tesoreria.folio}' + (f' · {referencia}' if referencia else '')
        renglones.append(Renglon(
            fecha=pago.fecha, tipo='pago', empresa=pago.empresa.nombre, referencia=referencia,
            concepto=pago.get_tipo_display().upper() + (f' · {pago.notas}' if pago.notas else ''),
            cargo=CERO, abono=pago.monto, cancelado=pago.cancelado, motivo_cancelacion=pago.motivo_cancelacion,
            id=pago.id, orden=(pago.fecha, 1, pago.creado),
        ))
    # Ventas: una por salida (y empresa del lote), con sus líneas como detalle.
    ventas = {}
    for linea in lineas_venta(clientes=[cliente.id]):
        empresa_linea = empresa_de(linea)
        if empresa is not None and empresa_linea.id != empresa.id:
            continue
        clave = (linea.salida_id, empresa_linea.id)
        if clave not in ventas:
            salida = linea.salida
            if salida.folio_de_salida:
                referencia = f'Salida {salida.folio_de_salida}' + (f' · Nota {salida.notas}' if salida.notas else '')
            else:
                referencia = f'Nota {salida.notas} · sin salida (todo en resguardo)'
            ventas[clave] = Renglon(
                fecha=salida.fecha, tipo='venta', empresa=empresa_linea.nombre, referencia=referencia, concepto='',
                cargo=CERO, abono=CERO, cancelado=False, motivo_cancelacion='', id=salida.id,
                orden=(salida.fecha, 1, salida.creado),
            )
        monto = importe(linea)
        estado_linea, _ = estado(linea)
        renglon = ventas[clave]
        renglon.cargo += monto or CERO
        # Concepto como en el estado de cuenta del área: cámara / proveedor del lote.
        proveedor = linea.entrada_detalle.proveedor_origen
        origen = '/'.join(filter(None, [linea.camara.nombre if linea.camara_id else '', proveedor.nombre if proveedor else '']))
        renglon.detalle.append({
            'linea_id': linea.id, 'folio_salida': linea.salida.folio_de_salida or '', 'nota': linea.salida.notas,
            'origen': origen, 'producto': producto_de(linea),
            'kilos': linea.total_kilos, 'precio_kilo': linea.precio_x_kilo, 'importe': monto, 'estado': estado_linea,
        })
    for renglon in ventas.values():
        renglon.concepto = ', '.join(sorted({d['producto'] for d in renglon.detalle}))
        if any(d['importe'] is None for d in renglon.detalle):
            renglon.concepto += ' · hay líneas sin precio'
        renglones.append(renglon)

    for ajuste in ajustes:
        concepto = ajuste.concepto
        if ajuste.es_traspaso:
            direccion = 'a' if ajuste.naturaleza == AjusteCliente.ABONO else 'desde'
            concepto = f'{concepto} (traspaso {direccion} {ajuste.contraparte.empresa.nombre})'
        if ajuste.notas:
            concepto += f' · {ajuste.notas}'
        cargo = ajuste.naturaleza == AjusteCliente.CARGO
        renglones.append(Renglon(
            fecha=ajuste.fecha, tipo='cargo' if cargo else 'abono', empresa=ajuste.empresa.nombre, referencia='',
            concepto=concepto, cargo=ajuste.monto if cargo else CERO, abono=CERO if cargo else ajuste.monto,
            cancelado=ajuste.cancelado, motivo_cancelacion=ajuste.motivo_cancelacion,
            id=ajuste.id, orden=(ajuste.fecha, 1, ajuste.creado),
        ))

    renglones.sort(key=lambda r: r.orden)
    saldo = CERO
    for renglon in renglones:
        if not renglon.cancelado:
            saldo += renglon.cargo - renglon.abono
            renglon.saldo = saldo
    return renglones


@dataclass
class FilaSaldo:
    cliente: Cliente
    perfil: ClienteCartera | None
    por_empresa: dict  # {empresa_id: saldo}
    conjunto: Decimal
    ultimo_pago: tuple | None  # (fecha, monto)


def saldos_de_clientes(texto='', solo_con_saldo=False, solo_activos=True):
    """Filas del reporte de saldos y las empresas (columnas), ordenadas por cliente."""
    empresas = list(Empresa.objects.order_by('nombre'))
    clientes = Cliente.objects.select_related('cartera').order_by('nombre')
    if solo_activos:
        clientes = clientes.filter(activo=True)
    if texto:
        clientes = clientes.filter(nombre__icontains=texto.strip())
    clientes = list(clientes)
    ids = [c.id for c in clientes]
    saldos = saldos_por_cliente(clientes=ids)
    ultimos = ultimos_pagos(clientes=ids)

    filas = []
    for cliente in clientes:
        perfil = getattr(cliente, 'cartera', None) if hasattr(cliente, 'cartera') else None
        por_empresa = {e.id: saldos[cliente.id][e.id] for e in empresas}
        conjunto = sum(por_empresa.values(), CERO)
        if solo_con_saldo and conjunto == 0 and all(v == 0 for v in por_empresa.values()):
            continue
        ultimo = ultimos.get(cliente.id)
        if ultimo is None and perfil and perfil.pago_previo_fecha:
            ultimo = (perfil.pago_previo_fecha, perfil.pago_previo_monto)
        filas.append(FilaSaldo(cliente, perfil, por_empresa, conjunto, ultimo))
    return empresas, filas
