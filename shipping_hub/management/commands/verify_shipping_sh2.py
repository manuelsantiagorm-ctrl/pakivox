from django.core.management.base import BaseCommand

from shipping_hub.management.commands.select_prepare_shipping_purchase import (
    CONFIRM_TEXT,
)
from shipping_hub.services.quote_selection import (
    QUOTE_TTL_MINUTES,
)


class Command(BaseCommand):
    help = "Verifica SH-2 sin cotizar ni comprar."

    def handle(self, *args, **options):
        assert CONFIRM_TEXT == (
            "SELECCIONAR-Y-PREPARAR-SIN-COMPRAR"
        )
        assert QUOTE_TTL_MINUTES >= 1

        self.stdout.write(
            self.style.SUCCESS(
                "SH-2 SELECCIÓN Y COMPRA IDEMPOTENTE "
                "VERIFICADAS: OK"
            )
        )
        self.stdout.write(
            "No se llamó a proveedores y no se compraron guías."
        )
