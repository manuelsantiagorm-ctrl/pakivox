import json

from django.core.management.base import BaseCommand, CommandError

from shipping_hub.services.concurrency_lab import (
    build_concurrency_report,
)


class Command(BaseCommand):
    help = (
        "Ejecuta el laboratorio seguro de concurrencia sin llamar "
        "proveedores ni modificar datos."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--strict", action="store_true")

    def handle(self, *args, **options):
        if not options["dry_run"]:
            raise CommandError(
                "Esta fase solo permite ejecución segura con --dry-run."
            )

        report = build_concurrency_report()

        if options["json"]:
            self.stdout.write(
                json.dumps(
                    report.as_dict(),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            self.stdout.write("CONCURRENCY LAB — DRY-RUN")
            self.stdout.write(
                f"Backend: {report.backend}"
            )
            self.stdout.write(
                "select_for_update: "
                f"{'sí' if report.supports_select_for_update else 'no'}"
            )
            self.stdout.write(
                "skip_locked: "
                f"{'sí' if report.supports_skip_locked else 'no'}"
            )

            for check in report.checks:
                marker = "OK" if check.passed else "FAIL"
                self.stdout.write(
                    f"[{marker}] {check.name}: {check.detail}"
                )

            self.stdout.write(
                "Duplicados de compras abiertas: "
                f"{report.open_purchase_duplicates}"
            )
            self.stdout.write(
                "Duplicados de eventos: "
                f"{report.provider_event_duplicates}"
            )
            self.stdout.write(
                "Leases activos de compras: "
                f"{report.active_purchase_leases}"
            )
            self.stdout.write(
                "Leases activos de eventos: "
                f"{report.active_event_leases}"
            )

            if report.passed:
                self.stdout.write(
                    self.style.SUCCESS("CONCURRENCY LAB: OK")
                )
                self.stdout.write(
                    "Duplicados detectados: 0"
                )
            else:
                self.stdout.write(
                    self.style.ERROR("CONCURRENCY LAB: FAIL")
                )

        if options["strict"] and not report.passed:
            raise CommandError(
                "El laboratorio detectó riesgos de concurrencia."
            )
