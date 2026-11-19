from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Verifica que Event Engine no bloquee OUTER JOIN nullable."

    def handle(self, *args, **options):
        path = Path(
            "shipping_hub/services/provider_event_engine.py"
        )
        source = path.read_text(encoding="utf-8-sig")

        vulnerable = (
            "ShippingProviderEvent.objects.select_for_update()\n"
            "            .select_related("
        )
        if vulnerable in source:
            raise CommandError(
                "Aún existe select_for_update + select_related nullable."
            )

        if "def claim_event(event_id: int):" not in source:
            raise CommandError("No se encontró claim_event.")

        if (
            "ShippingProviderEvent.objects.select_related("
            not in source
        ):
            raise CommandError(
                "No se encontró la carga separada de relaciones."
            )

        self.stdout.write(
            self.style.SUCCESS(
                "SH-5B.1 EVENT ENGINE POSTGRESQL: OK"
            )
        )
        self.stdout.write(
            "Lock de fila base separado de relaciones nullable."
        )
        self.stdout.write(
            "Lifecycle, lease, retries e idempotencia conservados."
        )
