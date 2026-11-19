from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from shipping_hub.models import (
    ShippingAuditEvent,
    ShippingPurchase,
    ShippingPurchaseEvent,
)
from shipping_hub.services.purchase_reconciliation import (
    reconcile_carrier_shipment,
)


_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,199}$")


@dataclass(frozen=True)
class ManualResolutionResult:
    completed: bool
    pending: bool
    message: str
    tracking_number: str = ""
    provider_shipment_id: str = ""


def _clean_provider_id(value: str) -> str:
    provider_id = str(value or "").strip()
    if not _PROVIDER_ID_RE.fullmatch(provider_id):
        raise ValidationError(
            "El ID de SkydropX tiene un formato inválido."
        )
    return provider_id


def resolve_skydropx_purchase_by_exact_id(
    *,
    company,
    carrier_shipment,
    purchase_id: int,
    provider_shipment_id: str,
    actor,
) -> ManualResolutionResult:
    provider_id = _clean_provider_id(provider_shipment_id)
    now = timezone.now()

    with transaction.atomic():
        purchase = (
            ShippingPurchase.objects.select_for_update()
            .filter(
                pk=purchase_id,
                company_id=company.pk,
                carrier_shipment_id=carrier_shipment.pk,
            )
            .first()
        )
        if purchase is None:
            raise ValidationError(
                "La compra no pertenece a esta empresa y envío."
            )
        if purchase.status == ShippingPurchase.STATUS_COMPLETED:
            return ManualResolutionResult(
                True,
                False,
                "La guía ya estaba recuperada.",
                carrier_shipment.master_tracking_number or "",
                purchase.provider_purchase_id
                or carrier_shipment.external_shipment_id
                or "",
            )
        if purchase.provider != "skydropx":
            raise ValidationError(
                "Esta herramienta solo aplica a compras de SkydropX."
            )
        if purchase.status not in {
            ShippingPurchase.STATUS_PROVIDER_PENDING,
            ShippingPurchase.STATUS_RECONCILING,
            ShippingPurchase.STATUS_MANUAL_REVIEW,
        }:
            raise ValidationError(
                "La compra no se encuentra en un estado recuperable."
            )
        if purchase.charge_status not in {
            ShippingPurchase.CHARGE_POSSIBLE,
            ShippingPurchase.CHARGE_CONFIRMED,
        }:
            raise ValidationError(
                "No existe un posible cobro que deba recuperarse."
            )
        if purchase.lease_expires_at and purchase.lease_expires_at > now:
            raise ValidationError(
                "La compra ya está siendo revisada por otro proceso."
            )
        if (
            purchase.provider_purchase_id
            and purchase.provider_purchase_id != provider_id
        ):
            raise ValidationError(
                "La compra ya tiene asociado otro ID de proveedor. "
                "No se reemplazó para proteger la trazabilidad."
            )

        purchase.lease_expires_at = now + timedelta(minutes=2)
        purchase.save(
            update_fields=["lease_expires_at", "updated_at"]
        )

        ShippingPurchaseEvent.objects.create(
            company_id=company.pk,
            purchase_id=purchase.pk,
            event_type="manual_resolution_requested",
            from_status=purchase.status,
            to_status=purchase.status,
            message=(
                "Un operador autorizado solicitó recuperar la guía "
                "mediante un ID exacto de SkydropX."
            ),
            metadata={
                "provider": "skydropx",
                "provider_shipment_id": provider_id,
                "operation": "manual_exact_provider_resolution",
            },
            actor=actor,
        )
        ShippingAuditEvent.objects.create(
            company_id=company.pk,
            shipping_order_id=carrier_shipment.shipping_order_id,
            carrier_shipment_id=carrier_shipment.pk,
            actor=actor,
            action="charged_guide_manual_resolution_requested",
            detail={
                "purchase_id": purchase.pk,
                "provider": "skydropx",
                "provider_shipment_id": provider_id,
            },
        )

    try:
        outcome = reconcile_carrier_shipment(
            shipment_id=carrier_shipment.pk,
            company_id=company.pk,
            purchase_id=purchase_id,
            provider_shipment_id_hint=provider_id,
            actor=actor,
        )
    except Exception:
        with transaction.atomic():
            locked = (
                ShippingPurchase.objects.select_for_update()
                .filter(
                    pk=purchase_id,
                    company_id=company.pk,
                    carrier_shipment_id=carrier_shipment.pk,
                )
                .first()
            )
            if locked is not None:
                locked.lease_expires_at = None
                locked.save(
                    update_fields=["lease_expires_at", "updated_at"]
                )
        raise

    with transaction.atomic():
        locked = (
            ShippingPurchase.objects.select_for_update()
            .get(
                pk=purchase_id,
                company_id=company.pk,
                carrier_shipment_id=carrier_shipment.pk,
            )
        )
        update_fields = [
            "lease_expires_at",
            "updated_at",
        ]

        # El valor escrito por el operador puede ser un tracking.
        # Solo guardamos el Shipment ID realmente confirmado por
        # la respuesta de conciliación.
        if (
            not locked.provider_purchase_id
            and outcome.provider_shipment_id
        ):
            locked.provider_purchase_id = (
                outcome.provider_shipment_id
            )
            update_fields.insert(
                0,
                "provider_purchase_id",
            )

        locked.lease_expires_at = None
        locked.save(update_fields=update_fields)

        ShippingPurchaseEvent.objects.create(
            company_id=company.pk,
            purchase_id=locked.pk,
            event_type=(
                "manual_resolution_completed"
                if outcome.completed
                else "manual_resolution_pending"
            ),
            from_status=locked.status,
            to_status=locked.status,
            message=outcome.message,
            metadata={
                "provider": "skydropx",
                "provider_shipment_id": (
                    outcome.provider_shipment_id or ""
                ),
                "submitted_reference": provider_id,
                "tracking_number": outcome.tracking_number or "",
                "operation": "manual_exact_provider_resolution",
            },
            actor=actor,
        )
        ShippingAuditEvent.objects.create(
            company_id=company.pk,
            shipping_order_id=carrier_shipment.shipping_order_id,
            carrier_shipment_id=carrier_shipment.pk,
            actor=actor,
            action=(
                "charged_guide_manual_resolution_completed"
                if outcome.completed
                else "charged_guide_manual_resolution_pending"
            ),
            detail={
                "purchase_id": locked.pk,
                "provider": "skydropx",
                "provider_shipment_id": (
                    outcome.provider_shipment_id or ""
                ),
                "submitted_reference": provider_id,
                "tracking_number": outcome.tracking_number or "",
                "completed": outcome.completed,
            },
        )

    return ManualResolutionResult(
        outcome.completed,
        outcome.pending,
        outcome.message,
        outcome.tracking_number,
        outcome.provider_shipment_id or "",
    )
