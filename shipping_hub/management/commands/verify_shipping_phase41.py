from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from shipping_hub.services.recovery_engine import (
    MAX_AUTOMATIC_RECOVERY_ATTEMPTS,
    RECOVERY_DELAYS_SECONDS,
    _next_retry_at,
)


class Command(BaseCommand):
    help = "Verifica la configuración del Recovery Engine sin tocar compras."

    def handle(self, *args, **options):
        assert MAX_AUTOMATIC_RECOVERY_ATTEMPTS == len(RECOVERY_DELAYS_SECONDS)
        assert RECOVERY_DELAYS_SECONDS[0] >= 15
        assert list(RECOVERY_DELAYS_SECONDS) == sorted(RECOVERY_DELAYS_SECONDS)

        before = timezone.now()
        next_run = _next_retry_at(attempt_count=1)
        after = timezone.now() + timedelta(seconds=RECOVERY_DELAYS_SECONDS[0] + 2)
        assert before < next_run < after

        self.stdout.write(
            self.style.SUCCESS("FASE 4.1 RECOVERY ENGINE VERIFICADA: OK")
        )
        self.stdout.write(
            "No se modificaron compras y no se llamó a ningún proveedor."
        )
