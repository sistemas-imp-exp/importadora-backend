from django.db import migrations


def backfill_proveedor_origen(apps, schema_editor):
    EntradaDetalle = apps.get_model('inventario', 'EntradaDetalle')
    MovimientoCamara = apps.get_model('inventario', 'MovimientoCamara')
    # Se procesa en orden de id: un lote "destino" de un MovimientoCamara siempre se
    # crea después que su lote de origen, así que para cuando le toca su turno, el
    # origen ya tiene proveedor_origen resuelto (aunque haya varios saltos entre
    # cámaras). IMPORTANTE: no se usa select_related para el origen — su
    # proveedor_origen pudo haberse actualizado apenas unas líneas atrás, en esta
    # misma corrida, y una consulta cacheada de antemano no lo reflejaría.
    for detalle in EntradaDetalle.objects.select_related('entrada').order_by('id'):
        if detalle.entrada.proveedor_id:
            EntradaDetalle.objects.filter(id=detalle.id).update(
                proveedor_origen_id=detalle.entrada.proveedor_id
            )
            continue

        movimiento = MovimientoCamara.objects.filter(entrada_detalle_destino_id=detalle.id).first()
        if movimiento:
            proveedor_origen_id = EntradaDetalle.objects.values_list(
                'proveedor_origen_id', flat=True
            ).get(id=movimiento.entrada_detalle_origen_id)
            EntradaDetalle.objects.filter(id=detalle.id).update(proveedor_origen_id=proveedor_origen_id)


def revertir(apps, schema_editor):
    EntradaDetalle = apps.get_model('inventario', 'EntradaDetalle')
    EntradaDetalle.objects.update(proveedor_origen=None)


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0010_entradadetalle_proveedor_origen'),
    ]

    operations = [
        migrations.RunPython(backfill_proveedor_origen, revertir),
    ]
