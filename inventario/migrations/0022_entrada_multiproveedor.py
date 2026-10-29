"""
Entradas con varios proveedores: proveedor, factura y pedimento pasan de la
cabecera de la entrada a cada línea, y el recibo de ingreso (LoteGeneral) deja
de pertenecer a una sola entrada (lo comparten las líneas de varias).

La carga inicial (cargar_inventario_inicial) ya juntaba proveedores distintos
en una entrada y dejaba la factura de cada línea en sus observaciones
("Factura de esta línea: X"): de ahí se recupera.
"""
import re
from collections import Counter

from django.db import migrations, models

FACTURA_EN_OBSERVACIONES = re.compile(r'Factura de esta línea: ([^.]+)')


def a_lineas(apps, schema_editor):
    Entrada = apps.get_model('inventario', 'Entrada')
    EntradaDetalle = apps.get_model('inventario', 'EntradaDetalle')
    Entrada.objects.filter(proveedor__isnull=True).update(es_traslado=True)
    for detalle in EntradaDetalle.objects.select_related('entrada').filter(entrada__es_traslado=False):
        entrada = detalle.entrada
        propia = FACTURA_EN_OBSERVACIONES.search(detalle.observaciones or '')
        detalle.factura = (propia.group(1).strip() if propia else entrada.factura)[:50]
        detalle.pedimento = entrada.pedimento
        if detalle.proveedor_origen_id is None:
            detalle.proveedor_origen_id = entrada.proveedor_id
        detalle.save(update_fields=['factura', 'pedimento', 'proveedor_origen'])


def a_cabecera(apps, schema_editor):
    Entrada = apps.get_model('inventario', 'Entrada')
    LoteGeneral = apps.get_model('inventario', 'LoteGeneral')
    for entrada in Entrada.objects.filter(es_traslado=False).prefetch_related('detalles'):
        lineas = list(entrada.detalles.all())
        if not lineas:
            continue
        entrada.proveedor_id = Counter(l.proveedor_origen_id for l in lineas).most_common(1)[0][0]
        entrada.factura = ', '.join(dict.fromkeys(l.factura for l in lineas if l.factura))[:50]
        entrada.pedimento = ', '.join(dict.fromkeys(l.pedimento for l in lineas if l.pedimento))[:50]
        entrada.save(update_fields=['proveedor', 'factura', 'pedimento'])
    for lote in LoteGeneral.objects.all():
        linea = lote.entradas_detalle.order_by('id').first()
        if linea is not None:
            lote.entrada_id = linea.entrada_id
            lote.save(update_fields=['entrada'])


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0021_decimales_kilos_y_precios'),
    ]

    operations = [
        migrations.AddField(
            model_name='entrada',
            name='es_traslado',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='entradadetalle',
            name='factura',
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name='entradadetalle',
            name='pedimento',
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AlterField(
            model_name='lotegeneral',
            name='entrada',
            field=models.ForeignKey(
                null=True, on_delete=models.deletion.PROTECT, related_name='lotes_generales', to='inventario.entrada',
            ),
        ),
        migrations.RunPython(a_lineas, a_cabecera),
        migrations.RemoveField(model_name='entrada', name='proveedor'),
        migrations.RemoveField(model_name='entrada', name='factura'),
        migrations.RemoveField(model_name='entrada', name='pedimento'),
        migrations.RemoveField(model_name='lotegeneral', name='entrada'),
    ]
