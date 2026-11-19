from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from shipping_hub.services.direct_state_write_scanner import (
    scan_shipping_hub,
)


class Command(BaseCommand):
    help = "Exige cero escrituras directas de estados Lifecycle."

    def handle(self, *args, **options):
        findings = scan_shipping_hub(
            Path(settings.BASE_DIR) / "shipping_hub"
        )
        if findings:
            for item in findings:
                self.stderr.write(
                    f"{item.file}:{item.line} | {item.kind} | "
                    f"{item.target} | {item.code}"
                )
            raise CommandError(
                f"Quedan {len(findings)} escrituras directas."
            )

        self.stdout.write(
            self.style.SUCCESS("ZERO DIRECT STATE WRITES: OK")
        )
        self.stdout.write(
            "Todas las transiciones operativas pasan por "
            "ShippingLifecycleService."
        )
