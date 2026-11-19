import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from shipping_hub.services.direct_state_write_scanner import (
    findings_as_dicts,
    scan_shipping_hub,
)


class Command(BaseCommand):
    help = (
        "Detecta escrituras directas de estados Lifecycle mediante AST. "
        "No modifica archivos ni datos."
    )

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true")
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Devuelve error si encuentra violaciones.",
        )

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR) / "shipping_hub"
        findings = scan_shipping_hub(root)

        if options["json"]:
            self.stdout.write(
                json.dumps(
                    {
                        "shipping_hub": str(root),
                        "count": len(findings),
                        "findings": findings_as_dicts(findings),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            if not findings:
                self.stdout.write(
                    self.style.SUCCESS(
                        "ZERO DIRECT STATE WRITES: OK"
                    )
                )
                self.stdout.write(
                    "Todas las transiciones operativas pasan por "
                    "ShippingLifecycleService."
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "ZERO DIRECT STATE WRITES: PENDIENTE"
                    )
                )
                self.stdout.write(
                    f"Violaciones candidatas encontradas: {len(findings)}"
                )
                for finding in findings:
                    self.stdout.write(
                        f"- {finding.file}:{finding.line} | "
                        f"{finding.kind} | {finding.target}"
                    )
                    self.stdout.write(
                        f"  {finding.code}"
                    )

        if findings and options["strict"]:
            raise CommandError(
                "Existen escrituras directas de estados Lifecycle."
            )
