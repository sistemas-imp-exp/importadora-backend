from django.db import migrations

EMPRESA_POR_DEFECTO = 'IMPORTADORA'
# Alta manual previa a la 0019, que creó IMPORTADORA aparte: es la misma empresa.
DUPLICADA = 'IMPORTADORA DE MARISCOS'


def quitar_duplicada(apps, schema_editor):
    """Pasa a IMPORTADORA lo que apunte a la empresa repetida (entradas y cámaras) y la borra."""
    Empresa = apps.get_model('inventario', 'Empresa')
    duplicada = Empresa.objects.filter(nombre=DUPLICADA).first()
    importadora = Empresa.objects.filter(nombre=EMPRESA_POR_DEFECTO).first()
    if duplicada is None or importadora is None:
        return
    apps.get_model('inventario', 'Entrada').objects.filter(empresa=duplicada).update(empresa=importadora)
    apps.get_model('inventario', 'Camara').objects.filter(empresa=duplicada).update(empresa=importadora)
    duplicada.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0019_entrada_empresa'),
    ]

    operations = [
        migrations.RunPython(quitar_duplicada, migrations.RunPython.noop),
    ]
