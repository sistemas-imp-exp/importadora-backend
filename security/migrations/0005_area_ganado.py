from django.db import migrations


def crear_area_ganado(apps, schema_editor):
    # Código del que dependen security.permissions.AreaGanado y PrivateRoute area="GAN".
    Area = apps.get_model("security", "Area")
    Area.objects.get_or_create(codigo="GAN", defaults={"nombre": "Ganado"})


class Migration(migrations.Migration):

    dependencies = [
        ("security", "0004_area_cartera"),
    ]

    operations = [
        migrations.RunPython(crear_area_ganado, migrations.RunPython.noop),
    ]
