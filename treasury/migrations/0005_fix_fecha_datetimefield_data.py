from datetime import timedelta

from django.db import migrations


def corregir_fecha(apps, schema_editor):
    """
    Antes de esta migración, MovimientoTesoreria.fecha era un DateField sin
    zona horaria (se guardaba como '2026-07-31', sin hora). Al convertirlo a
    DateTimeField, Django/SQLite reinterpretan ese valor asumiendo que es
    medianoche UTC, cuando en realidad representaba medianoche en
    America/Mexico_City (UTC-6). Esto recorre 6 horas hacia atrás la fecha
    mostrada. Aquí se corrige usando corte.fecha (que sí es un DateTimeField
    correcto desde siempre) como fuente de verdad; si algún movimiento no
    tuviera corte asociado, se le suman las 6 horas para compensar.
    """
    MovimientoTesoreria = apps.get_model('treasury', 'MovimientoTesoreria')

    for movimiento in MovimientoTesoreria.objects.select_related('corte').all():
        if movimiento.corte_id:
            movimiento.fecha = movimiento.corte.fecha
        else:
            movimiento.fecha = movimiento.fecha + timedelta(hours=6)
        movimiento.save(update_fields=['fecha'])


def revertir(apps, schema_editor):
    # No se puede revertir con certeza (se perdería la hora original),
    # se deja como no-op intencional.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('treasury', '0004_alter_movimientotesoreria_fecha'),
    ]

    operations = [
        migrations.RunPython(corregir_fecha, revertir),
    ]
