import json

from django.core.management.base import BaseCommand, CommandError

from shipping_hub.services.operations_center import (
    OperationsThresholds,
    collect_shipping_operations_metrics,
)


class Command(BaseCommand):
    help = (
        "Muestra el estado operativo del Shipping Hub. "
        "Es de solo lectura y nunca llama proveedores."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int)
        parser.add_argument("--provider")
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--waiting-minutes", type=int, default=5)
        parser.add_argument("--reconciling-minutes", type=int, default=15)
        parser.add_argument("--event-minutes", type=int, default=5)
        parser.add_argument("--possible-charge-minutes", type=int, default=10)
        parser.add_argument(
            "--fail-on-critical",
            action="store_true",
            help="Devuelve error cuando el estado global sea critical.",
        )

    def handle(self, *args, **options):
        numeric_options = (
            "waiting_minutes",
            "reconciling_minutes",
            "event_minutes",
            "possible_charge_minutes",
        )
        for name in numeric_options:
            if options[name] < 1:
                raise CommandError(f"--{name.replace('_', '-')} debe ser positivo.")

        metrics = collect_shipping_operations_metrics(
            company_id=options.get("company_id"),
            provider=options.get("provider"),
            thresholds=OperationsThresholds(
                waiting_minutes=options["waiting_minutes"],
                reconciling_minutes=options["reconciling_minutes"],
                event_minutes=options["event_minutes"],
                possible_charge_minutes=options[
                    "possible_charge_minutes"
                ],
            ),
        )

        if options["json"]:
            self.stdout.write(
                json.dumps(
                    metrics,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            health = metrics["health"]["status"].upper()
            style = (
                self.style.ERROR
                if health == "CRITICAL"
                else self.style.WARNING
                if health == "WARNING"
                else self.style.SUCCESS
            )
            self.stdout.write(style(f"ESTADO GLOBAL: {health}"))
            self.stdout.write(
                f"Compras totales: {metrics['purchases']['total']}"
            )
            self.stdout.write(
                f"Compras esperando: {metrics['purchases']['waiting']} "
                f"(atrasadas: {metrics['purchases']['waiting_stale']})"
            )
            self.stdout.write(
                f"Compras conciliando: "
                f"{metrics['purchases']['reconciling']} "
                f"(atrasadas: "
                f"{metrics['purchases']['reconciling_stale']})"
            )
            self.stdout.write(
                f"Posibles cobros: "
                f"{metrics['purchases']['possible_charge']} "
                f"(atrasados: "
                f"{metrics['purchases']['possible_charge_stale']})"
            )
            self.stdout.write(
                f"Revisión manual: "
                f"{metrics['purchases']['manual_review']}"
            )
            self.stdout.write(
                f"Leases de compra vencidos: "
                f"{metrics['purchases']['expired_leases']}"
            )
            self.stdout.write(
                f"Eventos totales: {metrics['events']['total']}"
            )
            self.stdout.write(
                f"Eventos pendientes: {metrics['events']['pending']} "
                f"(atrasados: {metrics['events']['stale']})"
            )
            self.stdout.write(
                f"Eventos en reintento: {metrics['events']['retry']}"
            )
            self.stdout.write(
                f"Dead letters: {metrics['events']['dead_letter']}"
            )
            self.stdout.write(
                f"Leases de evento vencidos: "
                f"{metrics['events']['expired_leases']}"
            )

            findings = metrics["health"]["findings"]
            if findings:
                self.stdout.write("")
                self.stdout.write("HALLAZGOS:")
                for finding in findings:
                    self.stdout.write(
                        f"- {finding['severity'].upper()} | "
                        f"{finding['code']} | "
                        f"{finding['message']} "
                        f"Cantidad={finding['count']}"
                    )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        "No se detectaron condiciones operativas de riesgo."
                    )
                )

        if (
            options["fail_on_critical"]
            and metrics["health"]["status"] == "critical"
        ):
            raise CommandError(
                "El Shipping Hub presenta condiciones críticas."
            )
