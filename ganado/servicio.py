"""
Reglas de los movimientos de ganado. Dónde está cada animal sale de su
último movimiento activo (orden fecha, id); por eso un movimiento nuevo no
puede ir antes del último del animal y solo se cancela el último de cada uno.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    ACTIVO, ALTA, BAJA, CANCELADO, MOTIVOS_POR_TIPO, MUERTE, MUERTO, TRASPASO, VENDIDO,
    Animal, Movimiento, MovimientoAnimal,
)

ESTADO_POR_MOTIVO = {MUERTE: MUERTO}


def _activos(animal_ids):
    """Movimientos activos de esos animales: {animal_id: [movimiento, ...]} en orden."""
    por_animal = {i: [] for i in animal_ids}
    lineas = (MovimientoAnimal.objects
              .filter(animal_id__in=animal_ids, movimiento__cancelado=False)
              .select_related('movimiento')
              .order_by('movimiento__fecha', 'movimiento_id'))
    for linea in lineas:
        por_animal[linea.animal_id].append(linea.movimiento)
    return por_animal


def destino(movimiento):
    """(rancho_id, estado) en que deja al animal un movimiento."""
    if movimiento.tipo == TRASPASO:
        return movimiento.rancho_destino_id, ACTIVO
    if movimiento.tipo == BAJA:
        return movimiento.rancho_id, ESTADO_POR_MOTIVO.get(movimiento.motivo, VENDIDO)
    return movimiento.rancho_id, ACTIVO


def recalcular(animal_ids):
    """Actualiza la copia de rancho y estado desde el último movimiento activo."""
    for animal_id, movimientos in _activos(list(animal_ids)).items():
        if movimientos:
            rancho_id, estado = destino(movimientos[-1])
            Animal.objects.filter(pk=animal_id).update(rancho_id=rancho_id, estado=estado)
        else:
            Animal.objects.filter(pk=animal_id).update(estado=CANCELADO)


def _validar_cabecera(datos):
    tipo, motivo, fecha = datos['tipo'], datos.get('motivo', ''), datos['fecha']
    errores = {}
    if motivo not in MOTIVOS_POR_TIPO[tipo]:
        errores['motivo'] = 'Elige el motivo.' if tipo != TRASPASO else 'Un traspaso no lleva motivo.'
    if fecha > timezone.localdate():
        errores['fecha'] = 'La fecha no puede ser futura.'
    rancho, destino_ = datos['rancho'], datos.get('rancho_destino')
    if tipo == TRASPASO:
        if destino_ is None:
            errores['rancho_destino_id'] = 'Indica el rancho destino.'
        elif destino_ == rancho:
            errores['rancho_destino_id'] = 'El destino debe ser otro rancho.'
        elif not destino_.activo:
            errores['rancho_destino_id'] = 'El rancho destino está inactivo.'
    elif destino_ is not None:
        errores['rancho_destino_id'] = 'Solo los traspasos llevan rancho destino.'
    if tipo == ALTA and not rancho.activo:
        errores['rancho_id'] = 'El rancho está inactivo.'
    if errores:
        raise ValidationError(errores)


def _validar_animales_en_rancho(animales, rancho, fecha):
    """Bajas y traspasos: cada animal activo en ese rancho y sin movimientos posteriores a la fecha."""
    if not animales:
        raise ValidationError({'animal_ids': 'Elige al menos un animal.'})
    historial = _activos([a.pk for a in animales])
    problemas = []
    for animal in animales:
        if animal.estado != ACTIVO or animal.rancho_id != rancho.pk:
            problemas.append(f'{animal.identificacion} no está activo en {rancho.nombre}')
        elif historial[animal.pk] and historial[animal.pk][-1].fecha > fecha:
            ultimo = historial[animal.pk][-1]
            problemas.append(f'{animal.identificacion} tiene un movimiento del {ultimo.fecha:%d/%m/%Y}, posterior a esta fecha')
    if problemas:
        # Un solo mensaje: la pantalla muestra el primero.
        raise ValidationError({'animal_ids': '; '.join(problemas) + '.'})


@transaction.atomic
def registrar(datos, animales_nuevos, animal_ids, usuario):
    """
    Alta: `animales_nuevos` = [{arete_1.., especie, categoria, sexo, raza,
    fecha_nacimiento, notas, cantidad}] (cantidad > 1 solo sin arete).
    Baja o traspaso: `animal_ids`.
    """
    _validar_cabecera(datos)
    tipo = datos['tipo']
    if tipo == ALTA:
        if not animales_nuevos:
            raise ValidationError({'animales': 'Agrega al menos un animal.'})
        if animal_ids:
            raise ValidationError({'animal_ids': 'Un alta registra animales nuevos.'})
        movimiento = Movimiento.objects.create(usuario=usuario, **datos)
        for renglon, datos_animal in enumerate(animales_nuevos, start=1):
            datos_animal = dict(datos_animal)
            cantidad = datos_animal.pop('cantidad', 1)
            for _ in range(cantidad):
                animal = Animal(usuario=usuario, rancho=movimiento.rancho, **datos_animal)
                try:
                    animal.full_clean(exclude=['folio'])
                except ValidationError as error:
                    # Se conserva el campo (arete_3, sexo…) y se dice en qué renglón.
                    raise ValidationError({
                        campo: [f'Renglón {renglon}: {m}' for m in mensajes]
                        for campo, mensajes in error.message_dict.items()
                    })
                animal.save()
                MovimientoAnimal.objects.create(movimiento=movimiento, animal=animal)
        return movimiento

    if animales_nuevos:
        raise ValidationError({'animales': 'Solo las altas registran animales nuevos.'})
    animales = list(Animal.objects.select_for_update().filter(pk__in=set(animal_ids)))
    if len(animales) != len(set(animal_ids)):
        raise ValidationError({'animal_ids': 'Algún animal no existe.'})
    _validar_animales_en_rancho(animales, datos['rancho'], datos['fecha'])
    movimiento = Movimiento.objects.create(usuario=usuario, **datos)
    MovimientoAnimal.objects.bulk_create(MovimientoAnimal(movimiento=movimiento, animal=a) for a in animales)
    recalcular([a.pk for a in animales])
    return movimiento


def _vecinos(movimiento):
    """Por animal: (fecha del movimiento anterior, fecha del siguiente) entre los activos."""
    resultado = {}
    for animal_id, movimientos in _activos(list(movimiento.lineas.values_list('animal_id', flat=True))).items():
        i = next(i for i, m in enumerate(movimientos) if m.pk == movimiento.pk)
        anterior = movimientos[i - 1].fecha if i > 0 else None
        siguiente = movimientos[i + 1].fecha if i + 1 < len(movimientos) else None
        resultado[animal_id] = (anterior, siguiente)
    return resultado


@transaction.atomic
def editar(movimiento, cambios, usuario):
    """Solo fecha, contraparte, importe y notas; los animales y ranchos no cambian (se cancela y recaptura)."""
    if movimiento.cancelado:
        raise ValidationError('El movimiento está cancelado.')
    fecha = cambios.get('fecha', movimiento.fecha)
    if fecha != movimiento.fecha:
        if fecha > timezone.localdate():
            raise ValidationError({'fecha': 'La fecha no puede ser futura.'})
        for anterior, siguiente in _vecinos(movimiento).values():
            if (anterior and fecha < anterior) or (siguiente and fecha > siguiente):
                raise ValidationError({'fecha': 'Con esa fecha quedaría fuera de orden con otros movimientos de sus animales.'})
    cambiado = False
    for campo in ('fecha', 'contraparte', 'importe', 'notas'):
        if campo in cambios and cambios[campo] != getattr(movimiento, campo):
            setattr(movimiento, campo, cambios[campo])
            cambiado = True
    if cambiado:
        movimiento.editado_por = usuario
        movimiento.editado_en = timezone.now()
        movimiento.save()
    return movimiento


@transaction.atomic
def cancelar(movimiento, motivo, usuario):
    if movimiento.cancelado:
        raise ValidationError('El movimiento ya está cancelado.')
    if not (motivo or '').strip():
        raise ValidationError({'motivo': 'Indica el motivo de la cancelación.'})
    animal_ids = list(movimiento.lineas.values_list('animal_id', flat=True))
    Animal.objects.select_for_update().filter(pk__in=animal_ids).exists()
    posteriores = [
        animal_id for animal_id, (_, siguiente) in _vecinos(movimiento).items() if siguiente is not None
    ]
    if posteriores:
        raise ValidationError(
            f'{len(posteriores)} animal(es) tienen movimientos posteriores: cancela primero esos.'
        )
    movimiento.cancelado = True
    movimiento.motivo_cancelacion = motivo.strip()
    movimiento.usuario_cancelacion = usuario
    movimiento.cancelado_en = timezone.now()
    movimiento.save()
    recalcular(animal_ids)
    return movimiento


def ubicacion_a_fecha(fecha, animal_ids=None):
    """
    {animal_id: (rancho_id, estado)} según el último movimiento activo hasta
    esa fecha; los animales sin movimientos a esa fecha no aparecen.
    """
    lineas = (MovimientoAnimal.objects
              .filter(movimiento__cancelado=False, movimiento__fecha__lte=fecha)
              .select_related('movimiento')
              .order_by('movimiento__fecha', 'movimiento_id'))
    if animal_ids is not None:
        lineas = lineas.filter(animal_id__in=animal_ids)
    ubicacion = {}
    for linea in lineas:
        ubicacion[linea.animal_id] = destino(linea.movimiento)
    return ubicacion
