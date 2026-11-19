from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from companies.models import Company
from shipping_hub.models import ShipmentPackage
from shipping_hub.services.cubication import prepare_cubication
from shipping_hub.services.multi_provider import (
    get_quote_integrations,
    quote_all_providers,
)
from shipping_hub.strategies.base import ShippingProviderError


CONFIRM_TEXT = "COTIZAR-SIN-COMPRAR"


def _money(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception as error:
        raise CommandError(
            "El proveedor devolvió un importe inválido."
        ) from error


class Command(BaseCommand):
    help = (
        "Valida un envío real y, con confirmación explícita, consulta "
        "tarifas sin seleccionar ni comprar ninguna guía."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int, required=True)
        parser.add_argument(
            "--shipping-order-id",
            type=int,
            required=True,
        )
        parser.add_argument(
            "--execute-live",
            action="store_true",
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
        company_id = options["company_id"]
        shipping_id = options["shipping_order_id"]
        selected_environment = (
            options["environment"]
            or getattr(
                settings,
                "SHIPPING_HUB_ENVIRONMENT",
                "production",
            )
        )

        company = Company.objects.filter(pk=company_id).first()
        if company is None:
            raise CommandError(
                "La empresa indicada no existe."
            )

        try:
            cubication = prepare_cubication(
                company=company,
                shipping_id=shipping_id,
                user=None,
                require_integration=True,
            )
        except ValidationError as error:
            raise CommandError(
                "; ".join(error.messages)
            ) from error

        if cubication.shipping_order.company_id != company.id:
            raise CommandError(
                "La orden de envío no pertenece a la empresa."
            )
        if cubication.order.company_id != company.id:
            raise CommandError(
                "El pedido no pertenece a la empresa."
            )
        if cubication.warehouse.company_id != company.id:
            raise CommandError(
                "La bodega no pertenece a la empresa."
            )

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
                "El envío no tiene bultos en borrador para cotizar."
            )

        integrations = list(
            get_quote_integrations(
                company=company,
                environment=selected_environment,
            )
        )
        if not integrations:
            raise CommandError(
                "No existen proveedores activos para este entorno."
            )

        self.stdout.write(
            "COTIZACIÓN CONTROLADA — PREFLIGHT"
        )
        self.stdout.write(
            f"Empresa: {company.id}"
        )
        self.stdout.write(
            f"Shipping order: {cubication.shipping_order.id}"
        )
        self.stdout.write(
            f"Pedido: {cubication.order.id}"
        )
        self.stdout.write(
            f"Bodega: {cubication.warehouse.id}"
        )
        self.stdout.write(
            f"Carrier shipment: {cubication.carrier_shipment.id}"
        )
        self.stdout.write(
            f"Bultos listos: {len(packages)}"
        )
        self.stdout.write(
            f"Entorno: {selected_environment}"
        )
        self.stdout.write(
            "Proveedores consultables: "
            + ", ".join(
                f"{item.provider}#{item.id}"
                for item in integrations
            )
        )

        if not options["execute_live"]:
            self.stdout.write(
                self.style.SUCCESS(
                    "PREFLIGHT DE COTIZACIÓN: OK"
                )
            )
            self.stdout.write(
                "No se llamó a proveedores, no se guardaron "
                "cotizaciones y no se compró ninguna guía."
            )
            self.stdout.write("")
            self.stdout.write(
                "Para consultar tarifas reales:"
            )
            self.stdout.write(
                "python manage.py quote_shipping_order "
                f"--company-id {company.id} "
                f"--shipping-order-id "
                f"{cubication.shipping_order.id} "
                "--execute-live "
                f'--confirm "{CONFIRM_TEXT}"'
            )
            return

        if options["confirm"] != CONFIRM_TEXT:
            raise CommandError(
                "Confirmación inválida. Usa exactamente: "
                f'--confirm "{CONFIRM_TEXT}"'
            )

        if (
            selected_environment == "production"
            and not getattr(
                settings,
                "SHIPPING_HUB_ALLOW_PRODUCTION_QUOTES",
                False,
            )
        ):
            raise CommandError(
                "Las cotizaciones de producción están bloqueadas "
                "por SHIPPING_HUB_ALLOW_PRODUCTION_QUOTES."
            )

        try:
            result = quote_all_providers(
                company=company,
                origin=cubication.origin_data,
                destination=cubication.destination_data,
                packages=packages,
                order_total=cubication.order.total,
                environment=selected_environment,
            )
        except ShippingProviderError as error:
            raise CommandError(
                f"{error} [{error.error_code}]"
            ) from error

        rates = result.get("rates") or []
        errors = result.get("errors") or []
        attempted = result.get("providers_attempted") or []

        if not rates:
            raise CommandError(
                "Los proveedores no devolvieron tarifas."
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "COTIZACIÓN REAL COMPLETADA: OK"
            )
        )
        self.stdout.write(
            "Proveedores intentados: "
            + ", ".join(attempted)
        )
        self.stdout.write(
            f"Tarifas recibidas: {len(rates)}"
        )

        for index, rate in enumerate(rates, start=1):
            provider = str(rate.get("provider") or "").strip()
            carrier = str(
                rate.get("carrier_name")
                or rate.get("carrier_code")
                or "Transportista"
            ).strip()
            service = str(
                rate.get("service_name")
                or rate.get("service_code")
                or "Servicio"
            ).strip()
            currency = str(
                rate.get("currency") or "MXN"
            ).strip()
            days = rate.get("estimated_delivery_days")
            amount = _money(rate.get("amount"))

            self.stdout.write(
                f"{index}. {provider} | {carrier} | "
                f"{service} | {currency} {amount} | "
                f"días={days if days is not None else '-'}"
            )

        if errors:
            self.stdout.write("")
            self.stdout.write("PROVEEDORES CON ERROR")
            for error in errors:
                self.stdout.write(
                    "- "
                    f"{error.get('provider')}: "
                    f"{error.get('message')} "
                    f"[{error.get('error_code')}]"
                )

        self.stdout.write("")
        self.stdout.write(
            "La consulta no seleccionó tarifa, no creó compra, "
            "no generó tracking y no compró ninguna guía."
        )
