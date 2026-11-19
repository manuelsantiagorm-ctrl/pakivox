import json

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError

from shipping_hub.services.provider_catalog import build_provider_catalog


class Command(BaseCommand):
    help = "Muestra el catálogo de paqueterías para una empresa sin revelar secretos."

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int, required=True)
        parser.add_argument("--environment", default="")
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--strict", action="store_true")

    def handle(self, *args, **options):
        Company = apps.get_model("companies", "Company")
        company = Company.objects.filter(pk=options["company_id"]).first()
        if company is None:
            raise CommandError("La empresa indicada no existe.")

        items = build_provider_catalog(
            company=company,
            environment=options["environment"] or None,
        )

        payload = [item.as_dict() for item in items]
        ready = [item for item in items if item.quote_ready]

        if options["json"]:
            self.stdout.write(
                json.dumps(payload, ensure_ascii=False, indent=2)
            )
        else:
            self.stdout.write(
                f"CATÁLOGO SHIPPING HUB — EMPRESA {company.pk}"
            )
            for item in items:
                marker = "LISTO" if item.quote_ready else item.status.upper()
                self.stdout.write(
                    f"[{marker}] {item.provider_name} | "
                    f"integración={item.integration_id or '-'} | "
                    f"entorno={item.environment} | "
                    f"credenciales={item.credential_mode}"
                )
                self.stdout.write(f"  {item.status_message}")

            self.stdout.write("")
            self.stdout.write(
                f"Proveedores listos para cotizar: {len(ready)}"
            )

        if options["strict"] and not ready:
            raise CommandError(
                "La empresa no tiene proveedores listos para cotizar."
            )
