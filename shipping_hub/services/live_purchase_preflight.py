from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from shipping_hub.models import (
    CarrierShipment,
    CompanyShippingIntegration,
    ShipmentPackage,
    ShippingPurchase,
)
from shipping_hub.services.quote_selection import validate_frozen_quote


@dataclass(frozen=True)
class PurchasePreflight:
    purchase_id: int
    company_id: int
    shipment_id: int
    shipping_order_id: int
    quote_id: int
    integration_id: int
    provider: str
    environment: str
    amount: Decimal
    currency: str
    package_count: int
    status: str
    charge_status: str
    idempotency_key: str
    integrity_sha256: str


def build_live_confirmation(preflight: PurchasePreflight) -> str:
    amount = preflight.amount.quantize(Decimal("0.01"))
    return (
        f"COMPRAR-GUIA-{preflight.purchase_id}-"
        f"{preflight.provider.upper()}-"
        f"{preflight.currency.upper()}-{amount}"
    )


def preflight_shipping_purchase(
    *,
    purchase_id: int,
    company_id: int,
) -> PurchasePreflight:
    if not purchase_id or purchase_id <= 0:
        raise ValidationError("Purchase ID inválido.")
    if not company_id or company_id <= 0:
        raise ValidationError("Company ID inválido.")

    with transaction.atomic():
        purchase = (
            ShippingPurchase.objects.select_for_update(of=("self",))
            .select_related(
                "carrier_shipment",
                "carrier_shipment__shipping_order",
                "selected_quote",
                "integration",
            )
            .get(
                pk=purchase_id,
                company_id=company_id,
            )
        )

        allowed_statuses = {
            ShippingPurchase.STATUS_CREATED,
            ShippingPurchase.STATUS_QUEUED,
        }
        if purchase.status not in allowed_statuses:
            raise ValidationError(
                "La compra no está disponible para ejecución. "
                f"Estado actual: {purchase.status}."
            )

        shipment = purchase.carrier_shipment
        quote = purchase.selected_quote
        integration = purchase.integration

        if shipment.company_id != company_id:
            raise ValidationError(
                "El envío pertenece a otra empresa."
            )
        if quote is None or quote.company_id != company_id:
            raise ValidationError(
                "La cotización no pertenece a la empresa."
            )
        if integration is None or integration.company_id != company_id:
            raise ValidationError(
                "La integración no pertenece a la empresa."
            )
        if not integration.is_active:
            raise ValidationError(
                "La integración está desactivada."
            )
        if (
            integration.account_mode
            == CompanyShippingIntegration.ACCOUNT_MODE_MASTER
        ):
            raise ValidationError(
                "Una cuenta maestra no puede comprar guías."
            )
        if quote.pk != shipment.selected_quote_id:
            raise ValidationError(
                "La cotización ya no es la seleccionada del envío."
            )
        if quote.integration_id != integration.pk:
            raise ValidationError(
                "La cotización pertenece a otra integración."
            )
        if quote.provider != integration.provider:
            raise ValidationError(
                "La cotización pertenece a otro proveedor."
            )
        if purchase.provider != integration.provider:
            raise ValidationError(
                "La compra contiene otro proveedor."
            )
        if shipment.master_tracking_number or shipment.has_label:
            raise ValidationError(
                "El envío ya tiene tracking o etiqueta."
            )
        if shipment.status != CarrierShipment.STATUS_GENERATING:
            raise ValidationError(
                "El envío no está en estado de generación segura."
            )
        if purchase.lease_expires_at is not None:
            raise ValidationError(
                "La compra tiene un lease activo o pendiente de revisión."
            )
        if purchase.quoted_amount != quote.amount:
            raise ValidationError(
                "El precio preparado no coincide con la cotización."
            )
        if (purchase.currency or "MXN").upper() != (
            quote.currency or "MXN"
        ).upper():
            raise ValidationError(
                "La moneda preparada no coincide con la cotización."
            )

        integrity = validate_frozen_quote(quote=quote)

        package_count = ShipmentPackage.objects.filter(
            company_id=company_id,
            carrier_shipment_id=shipment.pk,
        ).count()
        if package_count <= 0:
            raise ValidationError(
                "El envío no contiene bultos."
            )

        open_count = ShippingPurchase.objects.filter(
            company_id=company_id,
            carrier_shipment_id=shipment.pk,
            status__in=[
                ShippingPurchase.STATUS_CREATED,
                ShippingPurchase.STATUS_QUEUED,
                ShippingPurchase.STATUS_SENDING,
                ShippingPurchase.STATUS_PROVIDER_PENDING,
                ShippingPurchase.STATUS_RECONCILING,
                ShippingPurchase.STATUS_MANUAL_REVIEW,
            ],
        ).count()
        if open_count != 1:
            raise ValidationError(
                "Debe existir exactamente una compra abierta para el envío."
            )

        return PurchasePreflight(
            purchase_id=purchase.pk,
            company_id=company_id,
            shipment_id=shipment.pk,
            shipping_order_id=shipment.shipping_order_id,
            quote_id=quote.pk,
            integration_id=integration.pk,
            provider=purchase.provider,
            environment=purchase.environment,
            amount=purchase.quoted_amount,
            currency=(purchase.currency or "MXN").upper(),
            package_count=package_count,
            status=purchase.status,
            charge_status=purchase.charge_status,
            idempotency_key=str(purchase.idempotency_key),
            integrity_sha256=integrity,
        )
