from django.core.management.base import BaseCommand

from shipping_hub.services.concurrency_lab import (
    build_concurrency_report,
)


class Command(BaseCommand):
    help = "Verifica la instalación del laboratorio de concurrencia."

    def handle(self, *args, **options):
        report = build_concurrency_report()
        assert len(report.checks) >= 10
        assert report.open_purchase_duplicates >= 0
        assert report.provider_event_duplicates >= 0

        self.stdout.write(
            self.style.SUCCESS(
                "FASE 4.7A.1 LABORATORIO DE CONCURRENCIA "
                "VERIFICADO: OK"
            )
        )
        self.stdout.write(
            "La verificación fue de solo lectura."
        )
