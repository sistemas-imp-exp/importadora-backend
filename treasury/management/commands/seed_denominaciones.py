from decimal import Decimal

from django.core.management.base import BaseCommand

from treasury.models import Denominacion, Divisa

# Denominaciones estándar por código de divisa. Si una Divisa activa no
# aparece aquí, el comando la reporta y no le crea nada — hay que darlas de
# alta a mano (por Django admin) para monedas/billetes menos comunes.
DENOMINACIONES_POR_CODIGO = {
    "MXN": {
        Denominacion.BILLETE: [Decimal("1000"), Decimal("500"), Decimal("200"), Decimal("100"), Decimal("50"), Decimal("20")],
        Denominacion.MONEDA: [Decimal("20"), Decimal("10"), Decimal("5"), Decimal("2"), Decimal("1"), Decimal("0.50")],
    },
    "USD": {
        Denominacion.BILLETE: [Decimal("100"), Decimal("50"), Decimal("20"), Decimal("10"), Decimal("5"), Decimal("2"), Decimal("1")],
        Denominacion.MONEDA: [Decimal("0.25"), Decimal("0.10"), Decimal("0.05"), Decimal("0.01")],
    },
    "EUR": {
        Denominacion.BILLETE: [Decimal("200"), Decimal("100"), Decimal("50"), Decimal("20"), Decimal("10"), Decimal("5")],
        Denominacion.MONEDA: [Decimal("2"), Decimal("1"), Decimal("0.50"), Decimal("0.20"), Decimal("0.10"), Decimal("0.05"), Decimal("0.02"), Decimal("0.01")],
    },
}


class Command(BaseCommand):
    help = "Crea las denominaciones (billetes y monedas) estándar para las divisas activas conocidas (MXN, USD, EUR)."

    def handle(self, *args, **options):
        creadas = 0
        for divisa in Divisa.objects.filter(activa=True):
            tabla = DENOMINACIONES_POR_CODIGO.get(divisa.codigo)
            if not tabla:
                self.stdout.write(self.style.WARNING(
                    f"Sin denominaciones estándar para {divisa.codigo}: dalas de alta a mano en el admin."
                ))
                continue

            for tipo, valores in tabla.items():
                for valor in valores:
                    _, creada = Denominacion.objects.get_or_create(divisa=divisa, valor=valor, tipo=tipo)
                    if creada:
                        creadas += 1

        self.stdout.write(self.style.SUCCESS(f"Listo. {creadas} denominaciones nuevas creadas."))
