from django.db import migrations


def crear_area_cartera(apps, schema_editor):
    # Código del que dependen security.permissions.AreaCartera y PrivateRoute area="CAR".
    Area = apps.get_model("security", "Area")
    Area.objects.get_or_create(codigo="CAR", defaults={"nombre": "Cartera"})


class Migration(migrations.Migration):

    dependencies = [
        ("security", "0003_usuarioarea_solo_lectura"),
    ]

    operations = [
        migrations.RunPython(crear_area_cartera, migrations.RunPython.noop),
    ]
