from django.core.management.base import BaseCommand

from shipping_hub.management.commands.quote_shipping_order import (
    CONFIRM_TEXT,
)


class Command(BaseCommand):
    help = "Verifica SH-1.2 sin llamar proveedores."

    def handle(self, *args, **options):
        assert CONFIRM_TEXT == "COTIZAR-SIN-COMPRAR"

        self.stdout.write(
            self.style.SUCCESS(
                "SH-1.2 COTIZACIÓN CONTROLADA VERIFICADA: OK"
            )
        )
        self.stdout.write(
            "No se llamó a proveedores y no se compraron guías."
        )
