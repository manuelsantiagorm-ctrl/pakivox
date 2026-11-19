from __future__ import annotations

from decimal import Decimal
from typing import Any

from shipping_hub.models import ShipmentPackage
from shipping_hub.services.purchase_reconciliation import (
    ReconciliationOutcome,
    reconcile_carrier_shipment,
)
from shipping_hub.strategies.contracts import ProviderLabel, ProviderPurchaseResult


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (TypeError, ValueError):
        return None


def skydropx_outcome_to_provider_result(
    *,
    outcome: ReconciliationOutcome,
    shipment,
) -> ProviderPurchaseResult:
    """
    Convierte la conciliación SkydropX ya existente al contrato universal.

    La conciliación es la única responsable de descargar y guardar las etiquetas.
    Esta función únicamente comunica el resultado al Recovery Engine.
    """
    if not outcome.completed:
        return ProviderPurchaseResult(
            outcome="pending" if outcome.pending else "uncertain",
            retry_disposition="reconcile_only",
            provider_purchase_id=outcome.provider_shipment_id or "",
            tracking_numbers=(
                (outcome.tracking_number,) if outcome.tracking_number else ()
            ),
            charged_amount=_decimal(
                getattr(shipment, "total_cost", None)
                or getattr(shipment, "purchase_quote_amount", None)
            ),
            currency=str(getattr(shipment, "currency", None) or "MXN").upper(),
            error_code="skydropx_purchase_not_ready",
            error_message=outcome.message,
        )

    packages = list(
        ShipmentPackage.objects.filter(
            company_id=shipment.company_id,
            carrier_shipment_id=shipment.pk,
        ).order_by("package_number", "pk")
    )

    labels = tuple(
        ProviderLabel(
            tracking_number=str(package.tracking_number or "").strip(),
            label_url=str(package.label_url or "").strip(),
            label_format=str(package.label_format or "pdf").lower(),
            external_package_id=str(package.external_package_id or "").strip(),
        )
        for package in packages
    )
    tracking_numbers = tuple(
        dict.fromkeys(
            str(package.tracking_number or "").strip()
            for package in packages
            if str(package.tracking_number or "").strip()
        )
    )
    if not tracking_numbers and outcome.tracking_number:
        tracking_numbers = (outcome.tracking_number,)

    return ProviderPurchaseResult(
        outcome="completed",
        retry_disposition="reconcile_only",
        provider_purchase_id=(
            outcome.provider_shipment_id
            or str(shipment.external_shipment_id or "").strip()
        ),
        provider_reference=str(shipment.internal_reference or "").strip(),
        tracking_numbers=tracking_numbers,
        labels=labels,
        charged_amount=_decimal(
            shipment.total_cost or shipment.purchase_quote_amount
        ),
        currency=str(shipment.currency or "MXN").upper(),
        raw_response={
            "source": "skydropx_reconciliation",
            "shipment_id": shipment.pk,
            "tracking_number": outcome.tracking_number,
            "package_count": len(packages),
        },
    )


def recover_skydropx_purchase(*, purchase) -> ProviderPurchaseResult:
    """
    Busca y recupera una compra existente en SkydropX.

    SEGURIDAD:
    - Nunca llama create_shipment().
    - Nunca repite una compra.
    - Filtra siempre por company.
    - Reutiliza la conciliación que ya descargó exitosamente la guía real.
    """
    shipment = purchase.carrier_shipment
    if shipment.company_id != purchase.company_id:
        return ProviderPurchaseResult(
            outcome="failed_final",
            retry_disposition="manual_review",
            error_code="tenant_mismatch",
            error_message="La compra y el envío pertenecen a empresas diferentes.",
        )

    tracking_hint = str(
        shipment.master_tracking_number
        or (purchase.request_snapshot or {}).get("tracking_hint")
        or ""
    ).strip()

    outcome = reconcile_carrier_shipment(
        shipment_id=shipment.pk,
        company_id=purchase.company_id,
        purchase_id=purchase.pk,
        provider_shipment_id_hint=str(
            purchase.provider_purchase_id or ""
        ).strip(),
        tracking_hint=tracking_hint,
    )

    shipment.refresh_from_db()
    return skydropx_outcome_to_provider_result(
        outcome=outcome,
        shipment=shipment,
    )
