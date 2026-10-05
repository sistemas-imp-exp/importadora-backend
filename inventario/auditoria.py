"""
Edición restringida de entradas ya "cerradas" (con salidas registradas).

Una entrada que ya tuvo salidas no se puede editar por el camino normal: sus
cantidades sostienen el cálculo de existencias y las salidas ya emitidas. Pero
sí hay datos que se capturan mal y deben poder corregirse sin tocar esa
contabilidad — el número de factura, el lote del proveedor, la caducidad. Este
módulo define exactamente cuáles son y deja rastro de cada cambio en
`EdicionEntrada`.

Deliberadamente FUERA de la lista: cajas, total_kilos, peso_por_caja, producto,
camara, costo_por_kilo, fecha y proveedor. Todos alteran existencias, valuación
o trazabilidad ya consumida por documentos posteriores.
"""
from decimal import Decimal, InvalidOperation

from django.db import transaction

from .models import EdicionEntrada, Entrada, EntradaDetalle, LoteGeneral

# campo -> etiqueta legible (la usa el front para nombrar la columna)
CAMPOS_ENTRADA = {
    'factura': 'Factura',
    'pedimento': 'Pedimento',
}

CAMPOS_DETALLE = {
    'lote_proveedor': 'Lote proveedor',
    'precio_venta_planeado': 'Precio de venta planeado',
    'fecha_caducidad': 'Caducidad',
    'observaciones': 'Observaciones',
}

CAMPOS_MAYUSCULAS = {'factura', 'pedimento', 'lote_proveedor', 'observaciones'}

# ---------------------------------------------------------------------------
# Bitácora de las ediciones normales (módulo de Inventario)
#
# Lo de arriba es la vía restringida de superusuario. Esto de aquí registra las
# ediciones que sí se permiten por el camino normal — entradas que todavía no
# tienen salidas — para que Auditoría de entradas muestre TODO lo que se movió,
# no solo las correcciones del admin.
# ---------------------------------------------------------------------------

CAMPOS_CABECERA_BITACORA = {
    'fecha': 'Fecha',
    'empresa': 'Empresa',
    'proveedor': 'Proveedor',
    'es_internacional': 'Tipo de entrada',
    'factura': 'Factura',
    'pedimento': 'Pedimento',
}

CAMPOS_LINEA_BITACORA = {
    'producto': 'Producto',
    'lote_proveedor': 'Lote proveedor',
    'camara': 'Cámara',
    'cajas': 'Cajas',
    'peso_por_caja': 'Kg por caja',
    'total_kilos': 'Total kilos',
    'costo_por_kilo': 'Costo por kilo',
    'precio_venta_planeado': 'Precio de venta planeado',
    'fecha_caducidad': 'Caducidad',
    'observaciones': 'Observaciones',
}

ETIQUETAS_BITACORA = {
    **CAMPOS_CABECERA_BITACORA,
    **CAMPOS_LINEA_BITACORA,
    'recibo_ingreso': 'Recibo ingreso',
    'linea_agregada': 'Línea agregada',
    'linea_eliminada': 'Línea eliminada',
    'entrada_eliminada': 'Entrada eliminada',
}

MOTIVO_INVENTARIO = 'Edición desde el módulo de Inventario'
MOTIVO_ELIMINACION = 'Eliminación desde el módulo de Inventario'
MOTIVO_RECIBO = 'Recibo de ingreso capturado después de la entrada'


def referencia_entrada(entrada: Entrada) -> str:
    """Texto identificatorio de la entrada, para que la bitácora siga siendo
    legible cuando la entrada ya fue borrada y la FK quedó en NULL."""
    proveedor = entrada.proveedor.nombre if entrada.proveedor_id else 'Sin proveedor'
    return f"#{entrada.id} · {entrada.fecha} · {proveedor} · {entrada.factura or 'sin factura'}"


class EdicionInvalida(Exception):
    """Error de negocio al aplicar una edición auditada."""


def entrada_tiene_salidas(entrada: Entrada) -> bool:
    """
    ¿Alguna línea de la entrada ya fue vendida o movida a otra cámara?

    Un movimiento entre cámaras también genera un SalidaDetalle, así que este
    mismo filtro cubre los dos casos.
    """
    return entrada.detalles.filter(salidas_detalle__isnull=False).exists()


def _normalizar(campo, valor):
    if valor is None:
        return ''
    texto = str(valor).strip()
    return texto.upper() if campo in CAMPOS_MAYUSCULAS else texto


def _como_texto(valor):
    return '' if valor in (None, '') else str(valor)


def _aplicar_a_objeto(obj, campos_validos, cambios, registros, entrada, detalle, motivo, usuario):
    for campo, valor_nuevo in cambios.items():
        if campo not in campos_validos:
            raise EdicionInvalida(
                f"El campo '{campo}' no se puede editar por esta vía: altera la contabilidad del inventario."
            )

        anterior = getattr(obj, campo)
        nuevo = _normalizar(campo, valor_nuevo)

        # fecha_caducidad y precio_venta_planeado admiten vaciarse (None).
        if campo in ('fecha_caducidad', 'precio_venta_planeado') and nuevo == '':
            nuevo = None

        if _como_texto(anterior) == _como_texto(nuevo):
            continue

        setattr(obj, campo, nuevo)
        registros.append(EdicionEntrada(
            entrada=entrada,
            entrada_detalle=detalle,
            entrada_referencia=referencia_entrada(entrada),
            linea_referencia=_descripcion_linea(detalle) if detalle else '',
            campo=campo,
            valor_anterior=_como_texto(anterior),
            valor_nuevo=_como_texto(nuevo),
            motivo=motivo,
            editado_por=usuario,
        ))


@transaction.atomic
def aplicar_edicion(entrada: Entrada, datos: dict, usuario) -> list:
    """
    Aplica una edición auditada y devuelve los registros de bitácora creados.

    `datos` tiene la forma:
        {
            "motivo": "Factura mal capturada",
            "cabecera": {"factura": "FAC-123"},
            "lineas": [{"id": 12, "fecha_caducidad": "2026-08-01"}]
        }
    """
    motivo = (datos.get('motivo') or '').strip()
    if not motivo:
        raise EdicionInvalida('El motivo de la edición es obligatorio.')

    registros = []

    cabecera = datos.get('cabecera') or {}
    if cabecera:
        _aplicar_a_objeto(
            entrada, CAMPOS_ENTRADA, cabecera, registros,
            entrada=entrada, detalle=None, motivo=motivo, usuario=usuario,
        )

    detalles_modificados = []
    for item in datos.get('lineas') or []:
        detalle_id = item.get('id')
        try:
            detalle = entrada.detalles.get(id=detalle_id)
        except EntradaDetalle.DoesNotExist:
            raise EdicionInvalida(f'La línea {detalle_id} no pertenece a esta entrada.')

        cambios = {k: v for k, v in item.items() if k != 'id'}
        antes = len(registros)
        _aplicar_a_objeto(
            detalle, CAMPOS_DETALLE, cambios, registros,
            entrada=entrada, detalle=detalle, motivo=motivo, usuario=usuario,
        )
        if len(registros) > antes:
            detalles_modificados.append(detalle)

    if not registros:
        raise EdicionInvalida('No hay cambios que guardar.')

    entrada.save()
    for detalle in detalles_modificados:
        detalle.save()
    EdicionEntrada.objects.bulk_create(registros)

    return registros


def _valor_bitacora(obj, campo):
    """Valor legible de un campo: las FK se guardan por su nombre, no por id."""
    valor = getattr(obj, campo, None)
    if valor is None:
        return ''
    if campo == 'es_internacional':
        return 'Internacional' if valor else 'Nacional'
    return str(valor)


def _descripcion_linea(detalle):
    return f"{detalle.producto} · lote {detalle.lote_proveedor} · {detalle.cajas} cajas"


def tomar_snapshot(entrada: Entrada) -> dict:
    """Foto de los campos auditables ANTES de aplicar una edición normal."""
    return {
        'cabecera': {c: _valor_bitacora(entrada, c) for c in CAMPOS_CABECERA_BITACORA},
        'lineas': {
            d.id: {
                **{c: _valor_bitacora(d, c) for c in CAMPOS_LINEA_BITACORA},
                '__descripcion': _descripcion_linea(d),
            }
            for d in entrada.detalles.select_related('producto', 'camara')
        },
    }


def registrar_cambios(entrada: Entrada, snapshot: dict, usuario, motivo: str = MOTIVO_INVENTARIO) -> list:
    """
    Compara el estado actual de la entrada contra `snapshot` y deja una fila de
    bitácora por cada campo cambiado, más una por línea agregada o eliminada.
    """
    entrada.refresh_from_db()
    registros = []
    referencia = referencia_entrada(entrada)

    for campo in CAMPOS_CABECERA_BITACORA:
        anterior = snapshot['cabecera'].get(campo, '')
        actual = _valor_bitacora(entrada, campo)
        if anterior != actual:
            registros.append(EdicionEntrada(
                entrada=entrada, entrada_detalle=None,
                entrada_referencia=referencia, linea_referencia='', campo=campo,
                valor_anterior=anterior, valor_nuevo=actual,
                motivo=motivo, editado_por=usuario,
            ))

    lineas_actuales = {d.id: d for d in entrada.detalles.select_related('producto', 'camara')}

    for detalle_id, valores in snapshot['lineas'].items():
        if detalle_id not in lineas_actuales:
            # La línea eliminada ya no existe, así que no se puede referenciar
            # con la FK: su descripción queda en linea_referencia.
            registros.append(EdicionEntrada(
                entrada=entrada, entrada_detalle=None,
                entrada_referencia=referencia, linea_referencia=valores['__descripcion'],
                campo='linea_eliminada',
                valor_anterior=valores['__descripcion'], valor_nuevo='',
                motivo=motivo, editado_por=usuario,
            ))

    for detalle_id, detalle in lineas_actuales.items():
        anteriores = snapshot['lineas'].get(detalle_id)
        if anteriores is None:
            registros.append(EdicionEntrada(
                entrada=entrada, entrada_detalle=detalle,
                entrada_referencia=referencia, linea_referencia=_descripcion_linea(detalle),
                campo='linea_agregada',
                valor_anterior='', valor_nuevo=_descripcion_linea(detalle),
                motivo=motivo, editado_por=usuario,
            ))
            continue

        for campo in CAMPOS_LINEA_BITACORA:
            anterior = anteriores.get(campo, '')
            actual = _valor_bitacora(detalle, campo)
            if anterior != actual:
                registros.append(EdicionEntrada(
                    entrada=entrada, entrada_detalle=detalle,
                    entrada_referencia=referencia, linea_referencia=_descripcion_linea(detalle),
                    campo=campo,
                    valor_anterior=anterior, valor_nuevo=actual,
                    motivo=motivo, editado_por=usuario,
                ))

    if registros:
        EdicionEntrada.objects.bulk_create(registros)
    return registros


def registrar_eliminacion(entrada: Entrada, usuario, motivo: str = MOTIVO_ELIMINACION) -> EdicionEntrada:
    """
    Deja constancia de la baja ANTES de borrar la entrada. Las FK quedan en NULL
    al borrarse (SET_NULL), pero `entrada_referencia` conserva de qué se trataba.
    """
    lineas = ' | '.join(_descripcion_linea(d) for d in entrada.detalles.select_related('producto'))
    return EdicionEntrada.objects.create(
        entrada=entrada,
        entrada_detalle=None,
        entrada_referencia=referencia_entrada(entrada),
        linea_referencia='',
        campo='entrada_eliminada',
        valor_anterior=lineas,
        valor_nuevo='',
        motivo=motivo,
        editado_por=usuario,
    )


def recibos_de(entrada: Entrada) -> str:
    """Recibos de ingreso (IMP) de la entrada, sin repetir: "IMP-1, IMP-2"."""
    codigos = [lote.codigo for lote in entrada.lotes_generales.all()]
    return ', '.join(sorted(set(codigos)))


@transaction.atomic
def editar_recibos(entrada: Entrada, recibos: dict, usuario) -> list:
    """
    Asigna el recibo de ingreso (IMP) de cada línea que va a cámara, aunque la
    entrada ya tenga salidas: el recibo es un dato del documento y no altera
    cajas, kilos ni costo. Los lotes trasladados lo heredan solos (se lee del
    lote raíz, ver EntradaDetalle.documento_origen).

    `recibos` es {detalle_id: codigo}; un código vacío deja la línea sin recibo.
    Las líneas que comparten un código comparten su LoteGeneral, por lo que
    deben estar en la misma cámara. Deja una fila de bitácora por línea cambiada.
    """
    lineas = {d.id: d for d in entrada.detalles.select_related('producto', 'camara', 'lote_general')}
    nuevos = {}
    for detalle_id, codigo in recibos.items():
        linea = lineas.get(int(detalle_id))
        if linea is None:
            raise EdicionInvalida(f"La línea {detalle_id} no pertenece a esta entrada.")
        if not linea.camara_id:
            raise EdicionInvalida('Solo las líneas que van a cámara llevan recibo de ingreso.')
        nuevos[linea.id] = (codigo or '').strip().upper()

    camara_por_codigo = {}
    for detalle_id, codigo in nuevos.items():
        if not codigo:
            continue
        if len(codigo) > LoteGeneral._meta.get_field('codigo').max_length:
            raise EdicionInvalida(f"El recibo {codigo} es demasiado largo.")
        camara = lineas[detalle_id].camara_id
        if camara_por_codigo.setdefault(codigo, camara) != camara:
            raise EdicionInvalida(
                f"Las líneas con el recibo {codigo} deben estar en la misma cámara."
            )
    en_otra_entrada = (
        LoteGeneral.objects.filter(codigo__in=camara_por_codigo).exclude(entrada=entrada)
        .values_list('codigo', flat=True)
    )
    if en_otra_entrada:
        raise EdicionInvalida(f"El recibo {en_otra_entrada[0]} ya está registrado en otra entrada.")

    referencia = referencia_entrada(entrada)
    registros = []
    lotes = {lote.codigo: lote for lote in entrada.lotes_generales.all()}
    for detalle_id, codigo in nuevos.items():
        linea = lineas[detalle_id]
        anterior = linea.lote_general.codigo if linea.lote_general_id else ''
        if anterior == codigo:
            continue
        if codigo:
            lote = lotes.get(codigo)
            if lote is None:
                lote = lotes[codigo] = LoteGeneral.objects.create(
                    codigo=codigo, entrada=entrada, camara=linea.camara,
                    fecha_recibo=entrada.fecha, creado_por=usuario,
                )
            elif lote.camara_id != linea.camara_id:
                lote.camara = linea.camara
                lote.save(update_fields=['camara', 'modificado'])
        else:
            lote = None
        EntradaDetalle.objects.filter(id=linea.id).update(lote_general=lote)
        registros.append(EdicionEntrada(
            entrada=entrada, entrada_detalle=linea,
            entrada_referencia=referencia, linea_referencia=_descripcion_linea(linea),
            campo='recibo_ingreso', valor_anterior=anterior, valor_nuevo=codigo,
            motivo=MOTIVO_RECIBO, editado_por=usuario,
        ))

    # Un recibo que ya no agrupa ninguna línea se borra para liberar su código.
    entrada.lotes_generales.filter(entradas_detalle__isnull=True).delete()
    EdicionEntrada.objects.bulk_create(registros)
    return registros


MOTIVO_CAMPOS = 'Edición de campos desde Entradas'


@transaction.atomic
def editar_campos_lineas(entrada: Entrada, lineas: dict, usuario) -> list:
    """
    Edición de campos por línea que no altera la contabilidad del inventario,
    permitida aunque la entrada ya tenga salidas (botón "Edición de campos"):

        {detalle_id: {"recibo": "IMP-1", "precio_venta_planeado": "130.00"}}

    Cada clave es opcional. El recibo pasa por editar_recibos() (solo líneas en
    cámara); el precio de venta admite vaciarse. Todo deja fila en la bitácora.
    """
    detalles = {d.id: d for d in entrada.detalles.select_related('producto')}
    recibos, registros_precio = {}, []
    for detalle_id, cambios in lineas.items():
        if not isinstance(cambios, dict):
            raise EdicionInvalida('Formato inválido: cada línea lleva sus campos a editar.')
        detalle = detalles.get(int(detalle_id))
        if detalle is None:
            raise EdicionInvalida(f"La línea {detalle_id} no pertenece a esta entrada.")
        if 'recibo' in cambios:
            recibos[detalle.id] = cambios['recibo']
        if 'precio_venta_planeado' in cambios:
            precio = cambios['precio_venta_planeado']
            if precio not in (None, ''):
                try:
                    precio = Decimal(str(precio)).quantize(Decimal('0.01'))
                except (InvalidOperation, ValueError):
                    raise EdicionInvalida(f"Precio de venta inválido: {precio}.")
                if precio < 0 or precio >= Decimal('100000000'):
                    raise EdicionInvalida(f"Precio de venta fuera de rango: {precio}.")
            antes = len(registros_precio)
            _aplicar_a_objeto(
                detalle, {'precio_venta_planeado'}, {'precio_venta_planeado': precio},
                registros_precio, entrada, detalle, MOTIVO_CAMPOS, usuario,
            )
            if len(registros_precio) > antes:
                detalle.save(update_fields=['precio_venta_planeado'])

    EdicionEntrada.objects.bulk_create(registros_precio)
    registros_recibo = editar_recibos(entrada, recibos, usuario) if recibos else []
    return registros_precio + registros_recibo
