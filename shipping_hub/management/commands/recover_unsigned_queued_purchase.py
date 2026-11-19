from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from shipping_hub.models import (
    CarrierShipment,
    ShipmentPackage,
    ShippingPurchase,
)
from shipping_hub.services.lifecycle_service import (
    SOURCE_MANUAL_REPLAY,
    transition_package,
    transition_purchase,
    transition_shipment,
)


CONFIRM_TEXT = "CANCELAR-COLA-SIN-LLAMADA"


class Command(BaseCommand):
    help = (
        "Cancela y recupera una compra en cola que nunca fue tomada "
        "por un worker ni enviada al proveedor."
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
                .get(
                    pk=purchase_id,
                    company_id=company_id,
                )
            )

            if purchase.status not in {
                ShippingPurchase.STATUS_CREATED,
                ShippingPurchase.STATUS_QUEUED,
            }:
                raise CommandError(
                    f"La compra no está en created/queued: {purchase.status}."
                )

            if (
                purchase.attempt_count != 0
                or purchase.started_at is not None
                or purchase.processing_started_at is not None
                or purchase.provider_purchase_id
                or purchase.provider_request_id
            ):
                raise CommandError(
                    "La compra muestra señales de haber sido procesada. "
                    "Debe conciliarse, no cancelarse automáticamente."
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

            transition_purchase(
                entity_id=purchase.pk,
                company_id=company_id,
                to_state=ShippingPurchase.STATUS_CANCELLED,
                source=SOURCE_MANUAL_REPLAY,
                reason=(
                    "Compra cancelada antes de cualquier llamada al proveedor "
                    "porque la cotización carecía de firma de integridad."
                ),
                metadata={
                    "operation": "recover_unsigned_queued_purchase",
                    "attempt_count": purchase.attempt_count,
                },
            )

            purchase.charge_status = ShippingPurchase.CHARGE_NOT_CHARGED
            purchase.last_error_code = "unsigned_quote_cancelled"
            purchase.last_error_message = (
                "Compra cancelada antes de contactar al proveedor. "
                "Debe volver a seleccionarse una tarifa firmada."
            )
            purchase.save(
                update_fields=[
                    "charge_status",
                    "last_error_code",
                    "last_error_message",
                    "updated_at",
                ]
            )

            recovered_packages = 0
            for package in packages:
                if package.status == ShipmentPackage.STATUS_DRAFT:
                    continue
                if package.status != ShipmentPackage.STATUS_GENERATING:
                    raise CommandError(
                        f"Bulto {package.pk} en estado no recuperable: "
                        f"{package.status}."
                    )
                transition_package(
                    entity_id=package.pk,
                    company_id=company_id,
                    to_state=ShipmentPackage.STATUS_DRAFT,
                    source=SOURCE_MANUAL_REPLAY,
                    reason=(
                        "Recuperación de compra en cola nunca enviada "
                        "al proveedor."
                    ),
                    metadata={
                        "operation": "recover_unsigned_queued_purchase",
                        "purchase_id": purchase.pk,
                    },
                )
                recovered_packages += 1

            if shipment.status != CarrierShipment.STATUS_DRAFT:
                transition_shipment(
                    entity_id=shipment.pk,
                    company_id=company_id,
                    to_state=CarrierShipment.STATUS_DRAFT,
                    source=SOURCE_MANUAL_REPLAY,
                    reason=(
                        "Recuperación de compra en cola nunca enviada "
                        "al proveedor."
                    ),
                    metadata={
                        "operation": "recover_unsigned_queued_purchase",
                        "purchase_id": purchase.pk,
                    },
                )

        self.stdout.write(
            self.style.SUCCESS(
                "COMPRA EN COLA RECUPERADA SIN LLAMADA: OK"
            )
        )
        self.stdout.write(f"Purchase: {purchase_id}")
        self.stdout.write("Estado final: cancelled")
        self.stdout.write("Charge status: not_charged")
        self.stdout.write(
            f"Bultos recuperados: {recovered_packages}"
        )
        self.stdout.write("Proveedor llamado: NO")
        self.stdout.write("Cobro realizado: NO")
