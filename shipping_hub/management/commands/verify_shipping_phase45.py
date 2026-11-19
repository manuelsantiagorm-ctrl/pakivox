from django.core.management.base import BaseCommand

from shipping_hub.services.operations_center import (
    OperationsThresholds,
    collect_shipping_operations_metrics,
)


class Command(BaseCommand):
    help = "Verifica el Centro Operativo en modo de solo lectura."

    def handle(self, *args, **options):
        metrics = collect_shipping_operations_metrics(
            thresholds=OperationsThresholds(),
        )
        assert metrics["health"]["status"] in {
            "healthy",
            "warning",
            "critical",
        }
        assert "purchases" in metrics
        assert "events" in metrics
        assert "by_company_provider" in metrics["purchases"]
        assert "by_company_provider" in metrics["events"]

        self.stdout.write(
            self.style.SUCCESS(
                "FASE 4.5 CENTRO OPERATIVO VERIFICADO: OK"
            )
        )
        self.stdout.write(
            f"Estado actual: {metrics['health']['status'].upper()}."
        )
        self.stdout.write(
            "La verificación fue de solo lectura; no se llamó a proveedores."
        )
