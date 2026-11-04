from django.db import migrations

# Ranchos de docs/GANADO y categorías de sus hojas; el área agrega lo que falte.
RANCHOS = ['LA GLORIA', 'LOS COQUITOS', 'MEZQUITE', 'SAN JUDITAS', 'TRINIDAD']
CATEGORIAS = {
    'BOVINO': [
        ('VACA', 'H'), ('NOVILLA', 'H'), ('BECERRA', 'H'),
        ('TORO', 'M'), ('SEMENTAL', 'M'), ('NOVILLO', 'M'), ('BECERRO', 'M'),
    ],
    'EQUINO': [('YEGUA', 'H'), ('POTRANCA', 'H'), ('CABALLO', 'M'), ('POTRO', 'M')],
}


def sembrar(apps, schema_editor):
    Rancho = apps.get_model('ganado', 'Rancho')
    Especie = apps.get_model('ganado', 'Especie')
    Categoria = apps.get_model('ganado', 'Categoria')
    for nombre in RANCHOS:
        Rancho.objects.get_or_create(nombre=nombre)
    for especie, categorias in CATEGORIAS.items():
        especie, _ = Especie.objects.get_or_create(nombre=especie)
        for orden, (nombre, sexo) in enumerate(categorias, start=1):
            Categoria.objects.get_or_create(especie=especie, nombre=nombre, defaults={'sexo': sexo, 'orden': orden})


class Migration(migrations.Migration):

    dependencies = [
        ('ganado', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(sembrar, migrations.RunPython.noop),
    ]
