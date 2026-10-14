# Deudas como cuenta corriente: el monto capturado pasa a ser el monto inicial
# (lo que se debe sin salida de caja) y los movimientos ligados pueden ser
# cargos (egresos) además de abonos (ingresos).
#
# Se RENOMBRA la columna (no se borra y crea otra): las deudas ya registradas
# conservan su monto como monto inicial y sus abonos no cambian.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('treasury', '0018_historial_ediciones'),
    ]

    operations = [
        migrations.RenameField(
            model_name='deuda',
            old_name='monto',
            new_name='monto_inicial',
        ),
        migrations.AlterField(
            model_name='deuda',
            name='monto_inicial',
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=18,
                help_text='Lo que se debe sin salida de caja; 0 si la deuda nació de un egreso.',
            ),
        ),
        migrations.AlterField(
            model_name='movimientotesoreria',
            name='deuda',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='movimientos', to='treasury.deuda',
            ),
        ),
    ]
