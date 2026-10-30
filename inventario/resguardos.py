"""
Resguardos: producto ya vendido (y cobrado completo) que el cliente deja
guardado en nuestras cámaras y se lleva después en partes.

- La venta lo marca por línea (SalidaDetalle.kilos_resguardo): del lote solo
  sale lo entregado ese día (ver models.kilos_salidos).
- Cada retiro es otra Salida al mismo cliente, con folio propio (RES-0001),
  su folio de salida de cámara y su nota, de UNA sola venta. Sus líneas salen
  de cualquier lote del mismo producto (otro proveedor si hace falta) y se
  ligan a la línea de venta con `retiro_de`. No es venta: no suma en Cartera
  ni en Utilidad como venta propia.
- Lo pendiente de una línea = kilos_resguardo − kilos de sus retiros.

Inventario y Cartera usan el mismo apartado; cada retiro avisa al otro área.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, Exists, F, OuterRef, Prefetch, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from notificaciones.servicio import notificar
from security.permissions import AREA_CARTERA, AREA_INVENTARIO, tiene_area

from .models import Salida, SalidaDetalle

CERO = Decimal('0')
PREFIJO_FOLIO = 'RES-'

ESTADO_PENDIENTE = 'pendiente'
ESTADO_ENTREGADO = 'entregado'


def _retirado(relacion_salida):
    """Kilos ya retirados de las líneas en resguardo de la venta `relacion_salida` (OuterRef)."""
    return Coalesce(
        Subquery(
            SalidaDetalle.objects.filter(retiro_de__salida=OuterRef(relacion_salida))
            .values('retiro_de__salida').annotate(total=Sum('total_kilos')).values('total')[:1]
        ),
        Value(CERO),
        output_field=DecimalField(max_digits=15, decimal_places=3),
    )


def lineas_con_retirado():
    """Líneas de venta en resguardo con lo ya retirado anotado (`kilos_retirados`)."""
    return (
        SalidaDetalle.objects.filter(kilos_resguardo__gt=0)
        .select_related('producto')
        .annotate(kilos_retirados=Coalesce(
            Sum('retiros__total_kilos'), Value(CERO), output_field=DecimalField(max_digits=15, decimal_places=3),
        ))
        .order_by('id')
    )


def resguardos(params=None):
    """Ventas con resguardo, lo más reciente primero. Filtros: cliente, busqueda, estado."""
    params = params or {}
    retiros = (
        Salida.objects.filter(folio_retiro__isnull=False)
        # Un retiro es de una sola venta: la de cualquiera de sus líneas.
        .annotate(venta_id=Subquery(
            SalidaDetalle.objects.filter(salida=OuterRef('pk')).values('retiro_de__salida')[:1]
        ))
        .select_related('creado_por')
        .prefetch_related('detalles__producto', 'detalles__entrada_detalle__proveedor_origen', 'detalles__camara')
        .order_by('fecha', 'id')
    )
    queryset = (
        Salida.objects.filter(cliente__isnull=False, folio_retiro__isnull=True)
        .filter(Exists(SalidaDetalle.objects.filter(salida=OuterRef('pk'), kilos_resguardo__gt=0)))
        .select_related('cliente')
        .annotate(
            total_resguardo=Coalesce(
                Subquery(
                    SalidaDetalle.objects.filter(salida=OuterRef('pk'))
                    .values('salida').annotate(total=Sum('kilos_resguardo')).values('total')[:1]
                ),
                Value(CERO), output_field=DecimalField(max_digits=15, decimal_places=3),
            ),
            total_retirado=_retirado('pk'),
        )
        .prefetch_related(Prefetch('detalles', queryset=lineas_con_retirado(), to_attr='lineas_resguardo'))
        .order_by('-fecha', '-id')
    )

    cliente = params.get('cliente')
    if cliente:
        queryset = queryset.filter(cliente_id=cliente)
    estado = params.get('estado', ESTADO_PENDIENTE)
    if estado == ESTADO_PENDIENTE:
        queryset = queryset.filter(total_retirado__lt=F('total_resguardo'))
    elif estado == ESTADO_ENTREGADO:
        queryset = queryset.filter(total_retirado__gte=F('total_resguardo'))
    busqueda = (params.get('busqueda') or '').strip()
    if busqueda:
        # Folio y nota de la venta o de cualquiera de sus retiros.
        de_retiros = SalidaDetalle.objects.filter(retiro_de__salida=OuterRef('pk')).filter(
            Q(salida__folio_retiro__icontains=busqueda) | Q(salida__folio_de_salida__icontains=busqueda)
            | Q(salida__notas__icontains=busqueda)
        )
        queryset = queryset.filter(
            Q(folio_de_salida__icontains=busqueda) | Q(notas__icontains=busqueda)
            | Q(cliente__nombre__icontains=busqueda) | Exists(de_retiros)
        )
    return queryset, retiros


def _kg(valor):
    return str(Decimal(valor).quantize(Decimal('0.001')))


def serializar_retiro(retiro):
    lineas = list(retiro.detalles.all())
    return {
        'id': retiro.id,
        'folio_retiro': retiro.folio_retiro,
        'folio_de_salida': retiro.folio_de_salida,
        'notas': retiro.notas,
        'entrega_ligada': retiro.entrega_ligada,
        'fecha': retiro.fecha.isoformat(),
        'kilos': _kg(sum((l.total_kilos for l in lineas), CERO)),
        'cajas': sum(l.cajas for l in lineas),
        'registrado_por': retiro.creado_por.username if retiro.creado_por_id else None,
        'lineas': [
            {
                'retiro_de': l.retiro_de_id,
                'producto': f'{l.producto.talla} {l.producto.tipo}'.strip(),
                'lote_proveedor': l.entrada_detalle.lote_proveedor,
                'proveedor': l.entrada_detalle.proveedor_origen.nombre if l.entrada_detalle.proveedor_origen_id else '—',
                'camara': l.camara.nombre if l.camara_id else '—',
                'cajas': l.cajas,
                'kilos': _kg(l.total_kilos),
            }
            for l in lineas
        ],
    }


def serializar_resguardo(venta, retiros_por_venta):
    pendiente_total = venta.total_resguardo - venta.total_retirado
    return {
        'id': venta.id,
        'folio_de_salida': venta.folio_de_salida,
        'referencia': venta.referencia,
        'notas': venta.notas,
        'fecha': venta.fecha.isoformat(),
        'cliente': {'id': venta.cliente_id, 'nombre': venta.cliente.nombre},
        'kilos_resguardo': _kg(venta.total_resguardo),
        'kilos_retirados': _kg(venta.total_retirado),
        'kilos_pendientes': _kg(pendiente_total),
        'estado': ESTADO_PENDIENTE if pendiente_total > 0 else ESTADO_ENTREGADO,
        'lineas': [
            {
                'id': l.id,
                'producto_id': l.producto_id,
                'producto': f'{l.producto.talla} {l.producto.tipo}'.strip(),
                'cajas_resguardo': l.cajas_resguardo,
                'kilos_resguardo': _kg(l.kilos_resguardo),
                'kilos_retirados': _kg(l.kilos_retirados),
                'kilos_pendientes': _kg(l.kilos_resguardo - l.kilos_retirados),
            }
            for l in venta.lineas_resguardo
        ],
        'retiros': [serializar_retiro(r) for r in retiros_por_venta.get(venta.id, [])],
    }


def retiros_de(ventas, retiros):
    """{venta_id: [retiros]} de las ventas de la página (una consulta)."""
    resultado = {}
    for retiro in retiros.filter(venta_id__in=[v.id for v in ventas]):
        resultado.setdefault(retiro.venta_id, []).append(retiro)
    return resultado


def siguiente_folio():
    """RES-0001, RES-0002…: el consecutivo se calcula con los retiros bloqueados."""
    folios = Salida.objects.select_for_update().filter(folio_retiro__startswith=PREFIJO_FOLIO).values_list(
        'folio_retiro', flat=True,
    )
    numeros = [int(f[len(PREFIJO_FOLIO):]) for f in folios if f[len(PREFIJO_FOLIO):].isdigit()]
    return f'{PREFIJO_FOLIO}{max(numeros, default=0) + 1:04d}'


def area_de(usuario):
    """Desde qué área actúa: Cartera si solo escribe ahí; si no, Inventario (incluye al superusuario)."""
    if tiene_area(usuario, AREA_CARTERA, escritura=True) and not tiene_area(usuario, AREA_INVENTARIO, escritura=True):
        return AREA_CARTERA
    return AREA_INVENTARIO


def _avisar(retiro, venta, usuario, cancelado=False):
    origen = area_de(usuario)
    destino = AREA_INVENTARIO if origen == AREA_CARTERA else AREA_CARTERA
    ruta = '/cartera/resguardos' if destino == AREA_CARTERA else '/inventario/resguardos'
    kilos = sum((l.total_kilos for l in retiro.detalles.all()), CERO)
    accion = 'Se canceló el retiro' if cancelado else 'Retiro de resguardo'
    notificar(
        origen, destino, 'resguardo_retiro_cancelado' if cancelado else 'resguardo_retiro',
        f'{accion} {retiro.folio_retiro} · {venta.cliente.nombre}',
        f'Venta {venta.folio_de_salida or "sin salida"} (nota {venta.notas}) · {kilos.normalize():f} kg · '
        f'salida de cámara {retiro.folio_de_salida}, nota {retiro.notas}.',
        f'{ruta}?busqueda={venta.clave_busqueda}',
        usuario,
    )


@transaction.atomic
def registrar_retiro(datos, usuario):
    """`datos` ya validados por RetiroResguardoSerializer."""
    venta = datos['venta']
    retiro = Salida.objects.create(
        folio_de_salida=datos['folio_de_salida'], cliente=venta.cliente, fecha=datos['fecha'],
        notas=datos['notas'], folio_retiro=siguiente_folio(), creado_por=usuario,
    )
    for item in datos['detalles']:
        lote = item['entrada_detalle']
        SalidaDetalle.objects.create(
            salida=retiro, producto=lote.producto, entrada_detalle=lote, camara=lote.camara,
            cajas=item['cajas'], total_kilos=item['total_kilos'],
            factura_proveedor=lote.documento_origen['factura'], retiro_de=item['retiro_de'],
        )
    _avisar(retiro, venta, usuario)
    return retiro


def entregas_capturadas(venta, folio):
    """
    Líneas sin precio de la salida de cámara normal `folio` del cliente de la
    venta, de productos que la venta tiene en resguardo: entregas que se
    capturaron como salida normal y se pueden ligar como retiro.
    """
    return (
        SalidaDetalle.objects.filter(
            salida__folio_de_salida=folio, salida__folio_retiro__isnull=True, salida__es_resguardo=False,
            salida__cliente=venta.cliente, entrada_detalle__isnull=False,
            producto_id__in=venta.detalles.filter(kilos_resguardo__gt=0).values('producto_id'),
        )
        .filter(Q(precio_x_kilo__isnull=True) | Q(precio_x_kilo=0))
        .select_related('salida', 'producto', 'entrada_detalle__proveedor_origen', 'camara')
        .order_by('id')
    )


def _quitar_de_cartera(lineas):
    """Una línea que pasa a retiro deja de ser venta: fuera su verificación y su costo de Cartera."""
    from cartera.models import CostoVenta, VerificacionVenta
    VerificacionVenta.objects.filter(salida_detalle__in=lineas).delete()
    CostoVenta.objects.filter(salida_detalle__in=lineas).delete()


@transaction.atomic
def ligar_entregas(datos, usuario):
    """
    `datos` ya validados por LigarEntregaSerializer. Las líneas pasan de su
    salida normal a un retiro nuevo con el mismo folio de cámara, fecha y nota:
    conservan lote y kilos (las existencias no cambian) y dejan de ser venta.
    Si la salida se queda sin líneas, se borra (su folio sigue en el retiro).
    """
    venta, salida = datos['venta'], datos['salida']
    retiro = Salida.objects.create(
        folio_de_salida=salida.folio_de_salida, cliente=salida.cliente, fecha=salida.fecha, notas=salida.notas,
        folio_retiro=siguiente_folio(), entrega_ligada=True, creado_por=usuario,
    )
    lineas = [item['linea'] for item in datos['lineas']]
    _quitar_de_cartera(lineas)
    for item in datos['lineas']:
        linea = item['linea']
        linea.salida, linea.retiro_de = retiro, item['retiro_de']
        linea.precio_x_kilo = linea.total_venta = None
        linea.save(update_fields=['salida', 'retiro_de', 'precio_x_kilo', 'total_venta', 'modificado'])
    if not salida.detalles.exists():
        salida.delete()
    _avisar(retiro, venta, usuario)
    return retiro


@transaction.atomic
def cancelar_retiro(retiro, usuario):
    """
    Un retiro registrado se borra (su producto vuelve al lote). Uno ligado
    regresa sus líneas a la salida normal de su folio: el producto sí salió.
    """
    venta = retiro.detalles.select_related('retiro_de__salida__cliente').first().retiro_de.salida
    _avisar(retiro, venta, usuario, cancelado=True)
    if retiro.entrega_ligada:
        salida = Salida.objects.filter(
            folio_de_salida=retiro.folio_de_salida, folio_retiro__isnull=True, cliente=retiro.cliente,
        ).first() or Salida.objects.create(
            folio_de_salida=retiro.folio_de_salida, cliente=retiro.cliente, fecha=retiro.fecha,
            notas=retiro.notas, creado_por=retiro.creado_por,
        )
        retiro.detalles.update(salida=salida, retiro_de=None)
    retiro.delete()
