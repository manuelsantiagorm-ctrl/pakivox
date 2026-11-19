from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from shipping_hub.models import (
    CarrierShipment,
    ShipmentPackage,
    ShippingPurchase,
    ShippingPurchaseEvent,
)
from shipping_hub.services.lifecycle_service import transition_package
from shipping_hub.services.lifecycle_service import transition_shipment
from shipping_hub.services.lifecycle_service import SOURCE_MANUAL_REPLAY


CONFIRM_TEXT = "RECUPERAR-SIN-COBRO"


class Command(BaseCommand):
    help = (
        "Recupera un envío cuya compra terminó failed_final y not_charged. "
        "No revive la compra y no llama al proveedor."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int, required=True)
        parser.add_argument("--purchase-id", type=int, required=True)
        parser.add_argument("--confirm", default="")

    def handle(self, *args, **options):
        if options["confirm"] != CONFIRM_TEXT:
            raise CommandError(
                "Confirmación inválida. Usa exactamente: "
                f'--confirm "{CONFIRM_TEXT}"'
            )

        company_id = options["company_id"]
        purchase_id = options["purchase_id"]

        with transaction.atomic():
            purchase = (
                ShippingPurchase.objects.select_for_update()
                .select_related("carrier_shipment")
                .get(
                    pk=purchase_id,
                    company_id=company_id,
                )
            )

            if purchase.status != ShippingPurchase.STATUS_FAILED_FINAL:
                raise CommandError(
                    "La compra no está en failed_final."
                )

            if (
                purchase.charge_status
                != ShippingPurchase.CHARGE_NOT_CHARGED
            ):
                raise CommandError(
                    "La compra no está confirmada como no cobrada."
                )

            if purchase.provider_purchase_id:
                raise CommandError(
                    "La compra tiene identificador del proveedor."
                )

            shipment = (
                CarrierShipment.objects.select_for_update()
                .get(
                    pk=purchase.carrier_shipment_id,
                    company_id=company_id,
                )
            )

            if shipment.master_tracking_number or shipment.has_label:
                raise CommandError(
                    "El envío ya tiene tracking o etiqueta."
                )

            packages = list(
                ShipmentPackage.objects.select_for_update()
                .filter(
                    company_id=company_id,
                    carrier_shipment_id=shipment.pk,
                )
                .order_by("package_number", "pk")
            )
            if not packages:
                raise CommandError(
                    "El envío no contiene bultos."
                )

            changed = 0
            for package in packages:
                if package.status == ShipmentPackage.STATUS_DRAFT:
                    continue
                if package.status != ShipmentPackage.STATUS_GENERATING:
                    raise CommandError(
                        f"El bulto {package.pk} está en un estado "
                        f"no recuperable automáticamente: {package.status}."
                    )

                transition_package(
                    entity_id=package.pk,
                    company_id=company_id,
                    to_state=ShipmentPackage.STATUS_DRAFT,
                    source=SOURCE_MANUAL_REPLAY,
                    reason=(
                        "Recuperación segura después de una compra "
                        "fallida definitivamente y confirmada sin cobro."
                    ),
                    metadata={
                        "operation": "recover_failed_not_charged_purchase",
                        "purchase_id": purchase.pk,
                        "previous_error_code": purchase.last_error_code,
                    },
                )

                package.last_error_message = (
                    purchase.last_error_message or ""
                )
                package.save(
                    update_fields=[
                        "last_error_message",
                        "updated_at",
                    ]
                )
                changed += 1

            if shipment.status != CarrierShipment.STATUS_DRAFT:
                transition_shipment(
                    entity_id=shipment.pk,
                    company_id=company_id,
                    to_state=CarrierShipment.STATUS_DRAFT,
                    source=SOURCE_MANUAL_REPLAY,
                    reason=(
                        "Recuperación segura después de una compra "
                        "fallida definitivamente y sin cobro."
                    ),
                    metadata={
                        "operation": "recover_failed_not_charged_purchase",
                        "purchase_id": purchase.pk,
                    },
                )

            ShippingPurchaseEvent.objects.create(
                company_id=company_id,
                purchase=purchase,
                event_type="purchase_recovered_not_charged",
                from_status=purchase.status,
                to_status=purchase.status,
                message=(
                    "Se recuperaron envío y bultos para volver a cotizar. "
                    "La compra original permaneció cerrada."
                ),
                metadata={
                    "shipment_id": shipment.pk,
                    "recovered_packages": changed,
                    "charge_status": purchase.charge_status,
                    "provider_purchase_id": purchase.provider_purchase_id,
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                "RECUPERACIÓN SIN COBRO COMPLETADA: OK"
            )
        )
        self.stdout.write(f"Purchase original: {purchase_id}")
        self.stdout.write(
            "Purchase original permanece: failed_final"
        )
        self.stdout.write(
            f"Bultos recuperados a draft: {changed}"
        )
        self.stdout.write(
            "Proveedor llamado: NO"
        )
        self.stdout.write(
            "Guía comprada: NO"
        )
        self.stdout.write(
            "Cobro realizado: NO"
        )
