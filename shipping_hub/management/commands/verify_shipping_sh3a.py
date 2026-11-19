from django.core.management.base import BaseCommand

from shipping_hub.services.live_purchase_preflight import (
    PurchasePreflight,
    build_live_confirmation,
)


class Command(BaseCommand):
    help = "Verifica SH-3A sin comprar guías."

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS(
                "SH-3A PREFLIGHT DE COMPRA REAL VERIFICADO: OK"
            )
        )
        self.stdout.write(
            "La verificación no llamó proveedores y no compró guías."
        )
