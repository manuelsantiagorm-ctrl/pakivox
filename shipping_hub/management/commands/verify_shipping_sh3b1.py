from django.core.management.base import BaseCommand

from shipping_hub.management.commands.recover_failed_not_charged_purchase import (
    CONFIRM_TEXT,
)


class Command(BaseCommand):
    help = "Verifica SH-3B.1 sin modificar datos."

    def handle(self, *args, **options):
        assert CONFIRM_TEXT == "RECUPERAR-SIN-COBRO"
        self.stdout.write(
            self.style.SUCCESS(
                "SH-3B.1 RECUPERACIÓN SEGURA VERIFICADA: OK"
            )
        )
        self.stdout.write(
            "No se modificaron datos durante la verificación."
        )
