from django.db import migrations

# Códigos de los que dependen security.permissions y el ruteo del frontend
# (PrivateRoute area="TES"|"INV"|"ADMIN"). get_or_create por código: en una
# base donde ya existen no se duplican ni se renombran.
AREAS_BASE = [
    ("TES", "Tesorería"),
    ("INV", "Inventario"),
    ("ADMIN", "Administración"),
]


def crear_areas_base(apps, schema_editor):
    Area = apps.get_model("security", "Area")
    for codigo, nombre in AREAS_BASE:
        Area.objects.get_or_create(codigo=codigo, defaults={"nombre": nombre})


class Migration(migrations.Migration):

    dependencies = [
        ("security", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(crear_areas_base, migrations.RunPython.noop),
    ]
