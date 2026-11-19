from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from shipping_hub.services.direct_state_write_scanner import (
    scan_shipping_hub,
)


class Command(BaseCommand):
    help = "Verifica que el escáner AST pueda revisar Shipping Hub."

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR) / "shipping_hub"
        findings = scan_shipping_hub(root)

        self.stdout.write(
            self.style.SUCCESS(
                "FASE 4.6D.1 VERIFICADOR AST INSTALADO: OK"
            )
        )
        self.stdout.write(
            f"Violaciones candidatas actuales: {len(findings)}"
        )
        self.stdout.write(
            "La verificación fue de solo lectura."
        )
