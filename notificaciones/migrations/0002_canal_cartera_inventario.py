from django.db import migrations


def crear_canal(apps, schema_editor):
    # Primer canal entre áreas: Cartera ↔ Inventario (ventas contra nota).
    Area = apps.get_model("security", "Area")
    CanalArea = apps.get_model("notificaciones", "CanalArea")
    cartera = Area.objects.filter(codigo="CAR").first()
    inventario = Area.objects.filter(codigo="INV").first()
    if cartera and inventario:
        CanalArea.objects.get_or_create(area_a=cartera, area_b=inventario)


class Migration(migrations.Migration):

    dependencies = [
        ("notificaciones", "0001_initial"),
        ("security", "0004_area_cartera"),
    ]

    operations = [
        migrations.RunPython(crear_canal, migrations.RunPython.noop),
    ]
