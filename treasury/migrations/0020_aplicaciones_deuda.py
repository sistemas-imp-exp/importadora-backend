"""
Un movimiento puede repartirse entre varias deudas: la liga única
MovimientoTesoreria.deuda pasa a AplicacionDeuda (movimiento, deuda, monto).
Cada liga existente se copia con el monto completo del movimiento, así que
totales y saldos de las deudas no cambian.
"""
from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


def copiar_ligas(apps, schema_editor):
    MovimientoTesoreria = apps.get_model('treasury', 'MovimientoTesoreria')
    AplicacionDeuda = apps.get_model('treasury', 'AplicacionDeuda')
    ligados = MovimientoTesoreria.objects.filter(deuda__isnull=False).prefetch_related('divisas')
    AplicacionDeuda.objects.bulk_create([
        AplicacionDeuda(
            movimiento=movimiento,
            deuda_id=movimiento.deuda_id,
            monto=sum((linea.cantidad for linea in movimiento.divisas.all()), Decimal('0')),
        )
        for movimiento in ligados
    ])


def regresar_ligas(apps, schema_editor):
    # Solo es reversible mientras cada movimiento tenga una sola deuda.
    MovimientoTesoreria = apps.get_model('treasury', 'MovimientoTesoreria')
    AplicacionDeuda = apps.get_model('treasury', 'AplicacionDeuda')
    for aplicacion in AplicacionDeuda.objects.all():
        MovimientoTesoreria.objects.filter(pk=aplicacion.movimiento_id).update(deuda_id=aplicacion.deuda_id)


class Migration(migrations.Migration):

    dependencies = [
        ('treasury', '0019_deudas_cargos'),
    ]

    operations = [
        migrations.CreateModel(
            name='AplicacionDeuda',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('monto', models.DecimalField(decimal_places=2, max_digits=18)),
                ('deuda', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='aplicaciones', to='treasury.deuda')),
                ('movimiento', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='aplicaciones', to='treasury.movimientotesoreria')),
            ],
        ),
        migrations.RunPython(copiar_ligas, regresar_ligas),
        # Primero se quita la liga vieja: su related_name ('movimientos') es el
        # mismo que usa la relación nueva.
        migrations.RemoveField(
            model_name='movimientotesoreria',
            name='deuda',
        ),
        migrations.AddField(
            model_name='movimientotesoreria',
            name='deudas',
            field=models.ManyToManyField(blank=True, related_name='movimientos', through='treasury.AplicacionDeuda', to='treasury.deuda'),
        ),
        migrations.AddConstraint(
            model_name='aplicaciondeuda',
            constraint=models.UniqueConstraint(fields=('movimiento', 'deuda'), name='aplicacion_deuda_unica'),
        ),
        migrations.AddConstraint(
            model_name='aplicaciondeuda',
            constraint=models.CheckConstraint(condition=models.Q(('monto__gt', 0)), name='aplicacion_deuda_monto_positivo'),
        ),
    ]
