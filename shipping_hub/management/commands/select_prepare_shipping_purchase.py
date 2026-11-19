from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from companies.models import Company
from shipping_hub.models import (
    CompanyShippingIntegration,
    ShipmentPackage,
)
from shipping_hub.services.cubication import prepare_cubication
from shipping_hub.services.multi_provider import quote_all_providers
from shipping_hub.services.purchase_confirmation import (
    build_purchase_confirmation,
)
from shipping_hub.services.purchase_engine import (
    request_shipping_purchase,
)
from shipping_hub.services.quote_selection import (
    select_and_freeze_rate,
    validate_frozen_quote,
)
from shipping_hub.strategies.base import ShippingProviderError


CONFIRM_TEXT = "SELECCIONAR-Y-PREPARAR-SIN-COMPRAR"


class Command(BaseCommand):
    help = (
        "Cotiza, selecciona una tarifa real y prepara una compra "
        "idempotente en cola. No llama al endpoint de compra."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int, required=True)
        parser.add_argument(
            "--shipping-order-id",
            type=int,
            required=True,
        )
        parser.add_argument(
            "--rate-index",
            type=int,
            required=True,
        )
        parser.add_argument(
            "--confirm",
            default="",
        )
        parser.add_argument(
            "--environment",
            choices=("sandbox", "production"),
            default="",
        )

    def handle(self, *args, **options):
        if options["confirm"] != CONFIRM_TEXT:
            raise CommandError(
                "Confirmación inválida. Usa exactamente: "
                f'--confirm "{CONFIRM_TEXT}"'
            )

        environment = (
            options["environment"]
            or getattr(
                settings,
                "SHIPPING_HUB_ENVIRONMENT",
                "production",
            )
        )
        if (
            environment == "production"
            and not getattr(
                settings,
                "SHIPPING_HUB_ALLOW_PRODUCTION_QUOTES",
                False,
            )
        ):
            raise CommandError(
                "Las cotizaciones de producción están bloqueadas."
            )

        company = Company.objects.filter(
            pk=options["company_id"]
        ).first()
        if company is None:
            raise CommandError("La empresa no existe.")

        try:
            cubication = prepare_cubication(
                company=company,
                shipping_id=options["shipping_order_id"],
                user=None,
                require_integration=True,
            )
        except ValidationError as error:
            raise CommandError(
                "; ".join(error.messages)
            ) from error

        packages = list(
            ShipmentPackage.objects.for_company(company)
            .filter(
                carrier_shipment=cubication.carrier_shipment,
                status=ShipmentPackage.STATUS_DRAFT,
            )
            .order_by("package_number", "pk")
        )
        if not packages:
            raise CommandError(
                "El envío no tiene bultos en borrador."
            )

        try:
            result = quote_all_providers(
                company=company,
                origin=cubication.origin_data,
                destination=cubication.destination_data,
                packages=packages,
                order_total=cubication.order.total,
                environment=environment,
            )
        except ShippingProviderError as error:
            raise CommandError(
                f"{error} [{error.error_code}]"
            ) from error

        rates = result.get("rates") or []
        rate_index = options["rate_index"]
        if rate_index < 1 or rate_index > len(rates):
            raise CommandError(
                f"Índice inválido. Tarifas disponibles: {len(rates)}."
            )

        selected_rate = rates[rate_index - 1]

        try:
            quote = select_and_freeze_rate(
                company=company,
                cubication=cubication,
                rate=selected_rate,
                actor=None,
            )
            integrity = validate_frozen_quote(quote=quote)
        except ValidationError as error:
            raise CommandError(
                "; ".join(error.messages)
            ) from error

        integration = CompanyShippingIntegration.objects.get(
            pk=quote.integration_id,
            company=company,
            is_active=True,
        )

        token = build_purchase_confirmation(
            company=company,
            shipment=cubication.carrier_shipment,
            quote=quote,
            integration=integration,
        )

        try:
            purchase, created = request_shipping_purchase(
                company=company,
                user=None,
                cubication=cubication,
                integration=integration,
                confirmation_token=token,
            )
        except ValidationError as error:
            raise CommandError(
                "; ".join(error.messages)
            ) from error

        if purchase is None:
            raise CommandError(
                "El envío ya cuenta con tracking o etiqueta."
            )

        self.stdout.write(
            self.style.SUCCESS(
                "TARIFA SELECCIONADA: OK"
            )
        )
        self.stdout.write(
            f"Quote ID: {quote.id}"
        )
        self.stdout.write(
            f"Proveedor: {quote.provider}"
        )
        self.stdout.write(
            f"Transportista: {quote.carrier_name}"
        )
        self.stdout.write(
            f"Servicio: {quote.service_name}"
        )
        self.stdout.write(
            f"Importe congelado: {quote.currency} {quote.amount}"
        )
        self.stdout.write(
            f"Expira: {quote.expires_at.isoformat()}"
        )
        self.stdout.write(
            f"Integridad SHA256: {integrity}"
        )
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "COMPRA PREPARADA EN COLA: OK"
            )
        )
        self.stdout.write(
            f"Purchase ID: {purchase.id}"
        )
        self.stdout.write(
            f"Estado: {purchase.status}"
        )
        self.stdout.write(
            f"Creada ahora: {'sí' if created else 'no, idempotente'}"
        )
        self.stdout.write(
            f"Idempotency key: {purchase.idempotency_key}"
        )
        self.stdout.write("")
        self.stdout.write(
            "Proveedor llamado para cotizar: SÍ"
        )
        self.stdout.write(
            "Endpoint de compra llamado: NO"
        )
        self.stdout.write(
            "Guía comprada: NO"
        )
        self.stdout.write(
            "Tracking generado: NO"
        )
        self.stdout.write(
            "Etiqueta generada: NO"
        )
