from django.core.management.base import BaseCommand, CommandError

from shipping_hub.models import CompanyShippingIntegration, IntegrationSecret
from shipping_hub.services.secret_manager import (
    generate_secret,
    rotate_integration_secret,
)


class Command(BaseCommand):
    help = (
        "Crea o rota un secreto de webhook mediante WMS360 Secret Manager. "
        "Nunca imprime el valor completo."
    )

    def add_arguments(self, parser):
        parser.add_argument("--integration-id", type=int, required=True)
        parser.add_argument("--reason", default="Configuración administrativa")
        parser.add_argument("--generate", action="store_true")
        parser.add_argument("--value")
        parser.add_argument("--simulation", action="store_true")

    def handle(self, *args, **options):
        integration_id = options["integration_id"]
        value = options.get("value")
        generate = bool(options["generate"])
        simulation = bool(options["simulation"])

        if generate == bool(value):
            raise CommandError(
                "Usa exactamente una opción: --generate o --value."
            )

        integration = (
            CompanyShippingIntegration.objects
            .select_related("company")
            .filter(pk=integration_id)
            .first()
        )
        if integration is None:
            raise CommandError("No existe la integración indicada.")

        if generate:
            value = generate_secret()

        name = (
            "simulation_webhook_hmac"
            if simulation
            else "provider_webhook_hmac"
        )
        purpose = (
            "Firma HMAC exclusiva para simulaciones locales."
            if simulation
            else "Validación HMAC de webhooks del proveedor."
        )

        secret, version, changed = rotate_integration_secret(
            integration=integration,
            name=name,
            kind=IntegrationSecret.KIND_WEBHOOK_SECRET,
            value=value,
            reason=options["reason"],
            purpose=purpose,
            sync_legacy_webhook=True,
        )

        self.stdout.write(
            self.style.SUCCESS(
                "SECRETO CONFIGURADO CORRECTAMENTE."
                if changed
                else "EL SECRETO YA ERA LA VERSIÓN ACTIVA."
            )
        )
        self.stdout.write(
            f"Integración={integration.pk}, empresa={integration.company_id}, "
            f"proveedor={integration.provider}, nombre={secret.name}, "
            f"versión={version.version}, identificador={version.masked_hint}."
        )
        self.stdout.write(
            "El valor completo no se mostró ni se escribió en logs."
        )
