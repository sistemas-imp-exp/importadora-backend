"""
Caja por fecha: se retira el corte de caja abierto/cerrado.

- Nuevo AperturaPeriodo/AperturaDivisa (saldos iniciales por fecha).
- MovimientoTesoreria.fecha pasa a DateField (el día de la hoja física) y
  pierde el FK al corte; registra quién editó.
- ArqueoCaja toma la fecha de su corte y queda ligado a la fecha; varios por día.
- MovimientoArchivo: borrado con rastro (quién y cuándo).
- Se eliminan CorteCaja y SaldoCaja (los saldos ahora se calculan, ver saldos.py).

Los datos de Tesorería en producción eran de prueba; tras migrar se limpian
con `manage.py reset_datos_transaccionales` (ver manual de despliegue).
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def fecha_local_de_movimientos(apps, schema_editor):
    """
    El día de cada movimiento en hora de México. Se copia a una columna nueva en
    vez de cambiar el tipo en su lugar: truncar el datetime (guardado en UTC)
    podía correr un día los movimientos de la noche, y SQLite ni siquiera
    convierte el valor (lo deja ilegible como fecha).
    """
    MovimientoTesoreria = apps.get_model('treasury', 'MovimientoTesoreria')
    for movimiento in MovimientoTesoreria.objects.all():
        movimiento.fecha_dia = timezone.localtime(movimiento.fecha).date()
        movimiento.save(update_fields=['fecha_dia'])


def fecha_de_arqueos(apps, schema_editor):
    """Cada arqueo toma el día de su corte antes de que el corte desaparezca."""
    ArqueoCaja = apps.get_model('treasury', 'ArqueoCaja')
    for arqueo in ArqueoCaja.objects.select_related('corte'):
        arqueo.fecha = timezone.localtime(arqueo.corte.fecha).date()
        arqueo.save(update_fields=['fecha'])


class Migration(migrations.Migration):

    dependencies = [
        ('treasury', '0013_movimientotesoreria_movtesoreria_fecha_idx'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ------------------------------------------------ aperturas
        migrations.CreateModel(
            name='AperturaPeriodo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fecha', models.DateField(unique=True)),
                ('observaciones', models.TextField(blank=True)),
                ('creado', models.DateTimeField(auto_now_add=True)),
                ('modificado', models.DateTimeField(auto_now=True)),
                ('creado_por', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='aperturas_registradas', to=settings.AUTH_USER_MODEL)),
                ('editado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='aperturas_editadas', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-fecha']},
        ),
        migrations.CreateModel(
            name='AperturaDivisa',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('monto', models.DecimalField(decimal_places=2, max_digits=18)),
                ('apertura', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='saldos', to='treasury.aperturaperiodo')),
                ('divisa', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='treasury.divisa')),
            ],
            options={'unique_together': {('apertura', 'divisa')}},
        ),

        # ------------------------------------------------ arqueos: fecha desde su corte
        migrations.AddField(
            model_name='arqueocaja',
            name='fecha',
            field=models.DateField(null=True),
        ),
        migrations.RunPython(fecha_de_arqueos, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='arqueocaja',
            name='fecha',
            field=models.DateField(),
        ),
        migrations.RemoveField(model_name='arqueocaja', name='corte'),
        migrations.AddField(
            model_name='arqueocaja',
            name='editado_por',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='arqueos_editados', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='arqueocaja',
            name='editado_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterModelOptions(name='arqueocaja', options={'ordering': ['-fecha', '-hora_termino']}),

        # ------------------------------------------------ movimientos
        # El índice se quita y se vuelve a crear alrededor del cambio de tipo:
        # SQL Server no deja alterar una columna indexada.
        migrations.RemoveIndex(model_name='movimientotesoreria', name='movtesoreria_fecha_idx'),
        migrations.RemoveField(model_name='movimientotesoreria', name='corte'),
        migrations.AddField(
            model_name='movimientotesoreria',
            name='fecha_dia',
            field=models.DateField(null=True),
        ),
        migrations.RunPython(fecha_local_de_movimientos, migrations.RunPython.noop),
        migrations.RemoveField(model_name='movimientotesoreria', name='fecha'),
        migrations.RenameField(model_name='movimientotesoreria', old_name='fecha_dia', new_name='fecha'),
        migrations.AlterField(
            model_name='movimientotesoreria',
            name='fecha',
            field=models.DateField(),
        ),
        migrations.AddIndex(
            model_name='movimientotesoreria',
            index=models.Index(fields=['-fecha', '-creado'], name='movtesoreria_fecha_idx'),
        ),
        migrations.AddField(
            model_name='movimientotesoreria',
            name='editado_por',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='movimientos_editados', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='movimientotesoreria',
            name='editado_en',
            field=models.DateTimeField(blank=True, null=True),
        ),

        # ------------------------------------------------ adjuntos: borrado con rastro
        migrations.AddField(
            model_name='movimientoarchivo',
            name='eliminado',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='movimientoarchivo',
            name='eliminado_por',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='archivos_movimiento_eliminados', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='movimientoarchivo',
            name='eliminado_en',
            field=models.DateTimeField(blank=True, null=True),
        ),

        # ------------------------------------------------ fuera el corte de caja
        migrations.DeleteModel(name='SaldoCaja'),
        migrations.DeleteModel(name='CorteCaja'),
    ]
