from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from shipping_hub.models import ShippingPurchase
from shipping_hub.services.live_purchase_preflight import (
    build_live_confirmation,
    preflight_shipping_purchase,
)
from shipping_hub.services.purchase_engine import (
    process_shipping_purchase,
)


class Command(BaseCommand):
    help = (
        "Audita una compra preparada y, solo con confirmación exacta, "
        "ejecuta una única llamada de compra al proveedor."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int, required=True)
        parser.add_argument("--purchase-id", type=int, required=True)
        parser.add_argument("--execute-live", action="store_true")
        parser.add_argument("--confirm", default="")

    def handle(self, *args, **options):
        try:
            preflight = preflight_shipping_purchase(
                purchase_id=options["purchase_id"],
                company_id=options["company_id"],
            )
        except ShippingPurchase.DoesNotExist as error:
            raise CommandError(
                "La compra no existe dentro de la empresa indicada."
            ) from error
        except ValidationError as error:
            raise CommandError(
                "; ".join(error.messages)
            ) from error

        confirmation = build_live_confirmation(preflight)

        self.stdout.write(
            "SH-3A — PREFLIGHT DE COMPRA REAL"
        )
        self.stdout.write(
            f"Empresa: {preflight.company_id}"
        )
        self.stdout.write(
            f"Purchase ID: {preflight.purchase_id}"
        )
        self.stdout.write(
            f"Shipping order: {preflight.shipping_order_id}"
        )
        self.stdout.write(
            f"Carrier shipment: {preflight.shipment_id}"
        )
        self.stdout.write(
            f"Quote ID: {preflight.quote_id}"
        )
        self.stdout.write(
            f"Integración: {preflight.integration_id}"
        )
        self.stdout.write(
            f"Proveedor: {preflight.provider}"
        )
        self.stdout.write(
            f"Entorno: {preflight.environment}"
        )
        self.stdout.write(
            f"Importe máximo confirmado: "
            f"{preflight.currency} {preflight.amount}"
        )
        self.stdout.write(
            f"Bultos: {preflight.package_count}"
        )
        self.stdout.write(
            f"Estado: {preflight.status}"
        )
        self.stdout.write(
            f"Charge status: {preflight.charge_status}"
        )
        self.stdout.write(
            f"Idempotency key: {preflight.idempotency_key}"
        )
        self.stdout.write(
            f"Integridad SHA256: {preflight.integrity_sha256}"
        )

        if not options["execute_live"]:
            self.stdout.write("")
            self.stdout.write(
                self.style.SUCCESS(
                    "PREFLIGHT DE COMPRA REAL: OK"
                )
            )
            self.stdout.write(
                "No se llamó al endpoint de compra."
            )
            self.stdout.write(
                "No se compró ninguna guía."
            )
            self.stdout.write("")
            self.stdout.write(
                "Confirmación exacta requerida:"
            )
            self.stdout.write(confirmation)
            return

        if options["confirm"] != confirmation:
            raise CommandError(
                "La confirmación no coincide exactamente con "
                "Purchase ID, proveedor, moneda e importe."
            )

        result = process_shipping_purchase(preflight.purchase_id)

        purchase = ShippingPurchase.objects.select_related(
            "carrier_shipment"
        ).get(
            pk=preflight.purchase_id,
            company_id=preflight.company_id,
        )
        shipment = purchase.carrier_shipment

        self.stdout.write("")
        self.stdout.write(
            "RESULTADO DE LA EJECUCIÓN"
        )
        self.stdout.write(
            f"Purchase status: {purchase.status}"
        )
        self.stdout.write(
            f"Charge status: {purchase.charge_status}"
        )
        self.stdout.write(
            f"Provider purchase ID: "
            f"{purchase.provider_purchase_id or '-'}"
        )
        self.stdout.write(
            f"Tracking: "
            f"{shipment.master_tracking_number or '-'}"
        )
        self.stdout.write(
            f"Tiene etiqueta: "
            f"{'sí' if shipment.has_label else 'no'}"
        )
        self.stdout.write(
            f"Último error: "
            f"{purchase.last_error_code or '-'} "
            f"{purchase.last_error_message or ''}"
        )

        if purchase.status == ShippingPurchase.STATUS_COMPLETED:
            self.stdout.write(
                self.style.SUCCESS(
                    "COMPRA REAL COMPLETADA: OK"
                )
            )
        elif purchase.status in {
            ShippingPurchase.STATUS_PROVIDER_PENDING,
            ShippingPurchase.STATUS_RECONCILING,
            ShippingPurchase.STATUS_MANUAL_REVIEW,
        }:
            self.stdout.write(
                self.style.WARNING(
                    "COMPRA CON RESULTADO INCIERTO: "
                    "NO REPETIR. DEBE CONCILIARSE."
                )
            )
        elif purchase.status == ShippingPurchase.STATUS_FAILED_FINAL:
            self.stdout.write(
                self.style.ERROR(
                    "COMPRA RECHAZADA DEFINITIVAMENTE POR EL PROVEEDOR."
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "La compra terminó en un estado no concluyente. "
                    "No vuelvas a ejecutarla hasta revisar."
                )
            )
