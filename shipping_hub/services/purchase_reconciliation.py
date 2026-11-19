from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import requests
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from shipping_hub.models import (
    CarrierShipment,
    ShipmentPackage,
    ShippingAuditEvent,
    ShippingPurchase,
    ShippingPurchaseAttempt,
)
from shipping_hub.services.lifecycle_service import (
    SOURCE_RECONCILIATION,
    transition_package,
    transition_purchase,
    transition_shipment,
)
from shipping_hub.services.skydropx import SkydropxAPIError
from shipping_hub.strategies.resolver import get_shipping_strategy


@dataclass(frozen=True)
class ReconciliationOutcome:
    completed: bool
    pending: bool
    message: str
    tracking_number: str = ""
    provider_shipment_id: str = ""


def _rows(payload: Any) -> list[dict]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _skydropx_packages(payload: Any) -> list[dict]:
    result = []
    for item in (payload.get("included") or []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict) or str(item.get("type") or "").lower() not in {"package", "packages"}:
            continue
        attrs = item.get("attributes") or item
        result.append({
            "id": str(item.get("id") or attrs.get("id") or "").strip(),
            "tracking": str(attrs.get("tracking_number") or "").strip(),
            "tracking_url": str(attrs.get("tracking_url_provider") or "").strip(),
            "label_url": str(attrs.get("label_url") or "").strip(),
        })
    return result


def _string(value: Any) -> str:
    return str(value or "").strip()


def _normalized_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _parse_remote_datetime(value: Any):
    raw = _string(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(
            raw.replace("Z", "+00:00")
        )
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


def _quote_identifiers(quote) -> tuple[str, str]:
    if quote is None:
        return "", ""

    data = quote.response_data or {}
    raw_rate = data.get("raw_rate")
    raw_rate = raw_rate if isinstance(raw_rate, dict) else {}
    nested = raw_rate.get("response_data")
    nested = nested if isinstance(nested, dict) else {}

    quotation_id = _string(
        data.get("skydropx_quotation_id")
        or data.get("quotation_id")
        or raw_rate.get("quotation_id")
        or nested.get("skydropx_quotation_id")
        or nested.get("quotation_id")
    )
    rate_id = _string(quote.external_rate_id)
    return quotation_id, rate_id


def _payload_ids(payload: Any) -> set[str]:
    ids: set[str] = set()
    for row in _rows(payload):
        attrs = row.get("attributes") or row
        for value in (
            row.get("id"),
            attrs.get("id"),
            attrs.get("shipment_id"),
            attrs.get("shipmentId"),
        ):
            value = _string(value)
            if value:
                ids.add(value)
    return ids


def _candidate_score(
    *,
    item: dict,
    purchase,
    shipment,
    quotation_id: str,
    rate_id: str,
) -> tuple[int, tuple[str, ...]]:
    attrs = item.get("attributes") or item
    signals: list[str] = []
    score = 0

    remote_ref = _string(
        attrs.get("reference")
        or attrs.get("order_id")
        or attrs.get("client_reference")
        or attrs.get("idempotency_key")
    )
    local_refs = {
        _string(shipment.internal_reference),
        _string(shipment.idempotency_key),
        _string(getattr(purchase, "provider_reference", "")),
        _string(getattr(purchase, "idempotency_key", "")),
    }
    local_refs.discard("")

    if remote_ref and remote_ref in local_refs:
        score += 100
        signals.append("reference")

    remote_quotation = _string(
        attrs.get("quotation_id")
        or attrs.get("quotationId")
        or attrs.get("quotation")
    )
    if (
        quotation_id
        and remote_quotation
        and remote_quotation == quotation_id
    ):
        score += 45
        signals.append("quotation")

    remote_rate = _string(
        attrs.get("rate_id")
        or attrs.get("rateId")
        or attrs.get("rate")
    )
    if rate_id and remote_rate and remote_rate == rate_id:
        score += 35
        signals.append("rate")

    remote_amount = _normalized_decimal(
        attrs.get("total")
        or attrs.get("amount")
        or attrs.get("price")
        or attrs.get("total_price")
    )
    local_amount = _normalized_decimal(
        getattr(purchase, "quoted_amount", None)
        or shipment.purchase_quote_amount
        or shipment.total_cost
    )
    if (
        remote_amount is not None
        and local_amount is not None
        and remote_amount == local_amount
    ):
        score += 20
        signals.append("amount")

    remote_created = _parse_remote_datetime(
        attrs.get("created_at")
        or attrs.get("createdAt")
        or attrs.get("created")
    )
    local_created = getattr(purchase, "requested_at", None)
    if remote_created and local_created:
        difference = abs(
            (remote_created - local_created).total_seconds()
        )
        if difference <= 15 * 60:
            score += 20
            signals.append("time_15m")
        elif difference <= 2 * 60 * 60:
            score += 10
            signals.append("time_2h")

    return score, tuple(signals)


def _find_skydropx(
    shipment: CarrierShipment,
    *,
    purchase=None,
    tracking_hint: str = "",
) -> tuple[str, dict] | tuple[None, None]:
    integration = (
        shipment.selected_quote.integration
        if shipment.selected_quote_id
        else None
    )
    if (
        not integration
        or integration.company_id != shipment.company_id
    ):
        raise ValueError(
            "La integración logística del envío no está disponible."
        )

    client = get_shipping_strategy(
        integration=integration,
        company=shipment.company,
    )._client()

    exact_ids: list[str] = []

    for value in (
        getattr(purchase, "provider_purchase_id", ""),
        shipment.external_shipment_id,
    ):
        for remote_id in _string(value).split(","):
            remote_id = remote_id.strip()
            if remote_id and remote_id not in exact_ids:
                exact_ids.append(remote_id)

    # Las compras nuevas guardan la respuesta incompleta en Attempt.
    # También recuperamos cualquier ID remoto persistido allí.
    if purchase is not None:
        attempt_payloads = (
            ShippingPurchaseAttempt.objects.filter(
                company_id=shipment.company_id,
                purchase_id=purchase.pk,
            )
            .exclude(response_payload={})
            .order_by("-attempt_number", "-pk")
            .values_list("response_payload", flat=True)[:5]
        )
        for payload in attempt_payloads:
            for remote_id in _payload_ids(payload):
                if remote_id not in exact_ids:
                    exact_ids.append(remote_id)

    for remote_id in exact_ids:
        payload = client.get_shipment(remote_id)
        if payload:
            return remote_id, payload

    quotation_id, rate_id = _quote_identifiers(
        getattr(purchase, "selected_quote", None)
        or shipment.selected_quote
    )

    candidates: list[tuple[int, tuple[str, ...], str, dict]] = []

    for page in range(1, 21):
        payload = client.list_shipments(page=page)

        for item in _rows(payload):
            attrs = item.get("attributes") or item
            remote_id = _string(
                item.get("id")
                or attrs.get("id")
            )

            if not remote_id:
                continue

            detail = None
            remote_tracking = _string(
                attrs.get("master_tracking_number")
                or attrs.get("tracking_number")
            )

            if tracking_hint:
                # El listado de SkydropX no siempre expone el tracking
                # en el recurso raíz. Consultamos el detalle del envío
                # y revisamos tanto data como included/packages.
                detail = client.get_shipment(remote_id)
                detail_rows = _rows(detail)
                detail_attrs = (
                    detail_rows[0].get("attributes")
                    or detail_rows[0]
                    if detail_rows
                    else {}
                )
                detail_tracking = _string(
                    detail_attrs.get("master_tracking_number")
                    or detail_attrs.get("tracking_number")
                )
                package_trackings = {
                    _string(package.get("tracking"))
                    for package in _skydropx_packages(detail)
                    if _string(package.get("tracking"))
                }

                if (
                    remote_tracking != tracking_hint
                    and detail_tracking != tracking_hint
                    and tracking_hint not in package_trackings
                ):
                    continue

            score, signals = _candidate_score(
                item=item,
                purchase=purchase,
                shipment=shipment,
                quotation_id=quotation_id,
                rate_id=rate_id,
            )

            if tracking_hint:
                score += 200
                signals = signals + ("tracking",)

            # Regla conservadora:
            # - referencia o tracking exactos son suficientes; o
            # - deben existir al menos dos señales independientes y
            #   una puntuación mínima de 55.
            strong_identity = bool(
                {"reference", "tracking"} & set(signals)
            )
            if not strong_identity and (
                score < 55 or len(set(signals)) < 2
            ):
                continue

            if detail is None:
                detail = client.get_shipment(remote_id)

            if detail:
                candidates.append(
                    (score, signals, remote_id, detail)
                )

        meta = payload.get("meta") if isinstance(payload, dict) else {}
        if not (meta or {}).get("next_page"):
            break

    if not candidates:
        return None, None

    candidates.sort(
        key=lambda row: (-row[0], row[2])
    )
    best_score = candidates[0][0]
    best = [
        candidate
        for candidate in candidates
        if candidate[0] == best_score
    ]

    # Nunca elegimos arbitrariamente entre dos operaciones igualmente
    # compatibles. La compra permanece bloqueada para revisión.
    if len(best) != 1:
        return None, None

    _score, _signals, remote_id, detail = best[0]
    return remote_id, detail


def reconcile_carrier_shipment(
    *,
    shipment_id: int,
    company_id: int | None = None,
    purchase_id: int | None = None,
    provider_shipment_id_hint: str = "",
    tracking_hint: str = "",
    actor=None,
) -> ReconciliationOutcome:
    qs = CarrierShipment.objects.select_related(
        "company",
        "selected_quote__integration",
        "shipping_order",
    )
    if company_id is not None:
        qs = qs.filter(company_id=company_id)
    shipment = qs.get(pk=shipment_id)

    if shipment.master_tracking_number and shipment.status == CarrierShipment.STATUS_LABEL_READY:
        return ReconciliationOutcome(True, False, "La guía ya estaba conciliada.", shipment.master_tracking_number, shipment.external_shipment_id)

    provider = shipment.purchase_provider or shipment.provider
    if provider != "skydropx":
        return ReconciliationOutcome(False, True, f"El proveedor {provider} todavía no implementa conciliación automática; la compra permanece bloqueada para evitar un doble cargo.")

    purchase = None
    if purchase_id is not None:
        purchase = (
            ShippingPurchase.objects.select_related(
                "selected_quote",
            )
            .filter(
                pk=purchase_id,
                company_id=shipment.company_id,
                carrier_shipment_id=shipment.pk,
            )
            .first()
        )
        if purchase is None:
            raise ValueError(
                "La compra no pertenece a la empresa y envío indicados."
            )

    exact_provider_id = _string(provider_shipment_id_hint)
    if exact_provider_id:
        integration = (
            shipment.selected_quote.integration
            if shipment.selected_quote_id
            else None
        )
        if (
            not integration
            or integration.company_id != shipment.company_id
        ):
            raise ValueError(
                "La integración logística del envío no está disponible."
            )
        client = get_shipping_strategy(
            integration=integration,
            company=shipment.company,
        )._client()
        provider_id = exact_provider_id

        try:
            payload = client.get_shipment(exact_provider_id)
        except SkydropxAPIError as error:
            # Puede ser un tracking visible y no el Shipment ID interno.
            # Solo un 404 exacto habilita la búsqueda por tracking.
            # Errores de autenticación, límite, conexión o servidor
            # siguen propagándose y nunca se disfrazan de "no encontrado".
            if error.status_code != 404:
                raise

            provider_id, payload = _find_skydropx(
                shipment,
                purchase=purchase,
                tracking_hint=exact_provider_id,
            )
    else:
        provider_id, payload = _find_skydropx(
            shipment,
            purchase=purchase,
            tracking_hint=tracking_hint,
        )
    if not payload:
        return ReconciliationOutcome(False, True, "SkydropX todavía no devolvió una coincidencia segura. No se realizó otra compra.")

    remote_packages = _skydropx_packages(payload)
    if tracking_hint:
        matches = [p for p in remote_packages if p["tracking"] == tracking_hint]
        if matches:
            remote_packages = matches
    if not remote_packages or not all(p["tracking"] and p["label_url"] for p in remote_packages):
        return ReconciliationOutcome(
            False,
            True,
            (
                "La guía existe, pero SkydropX todavía no entrega "
                "rastreo y PDF completos."
            ),
            provider_shipment_id=provider_id or "",
        )

    pdfs = []
    for item in remote_packages:
        response = requests.get(item["label_url"], timeout=30)
        response.raise_for_status()
        if not response.content.startswith(b"%PDF"):
            raise ValueError("SkydropX devolvió una etiqueta que no es un PDF válido.")
        pdfs.append(response.content)

    root = _rows(payload)
    attrs = (root[0].get("attributes") or root[0]) if root else {}
    try:
        actual_cost = Decimal(str(attrs.get("total") or shipment.total_cost or shipment.purchase_quote_amount or 0))
    except (InvalidOperation, TypeError):
        actual_cost = shipment.total_cost

    with transaction.atomic():
        locked = CarrierShipment.objects.select_for_update().get(pk=shipment.pk, company_id=shipment.company_id)
        if locked.master_tracking_number or locked.status == CarrierShipment.STATUS_LABEL_READY:
            return ReconciliationOutcome(True, False, "La guía ya fue conciliada por otro proceso.", locked.master_tracking_number, locked.external_shipment_id)
        local_packages = list(ShipmentPackage.objects.select_for_update().filter(company_id=locked.company_id, carrier_shipment=locked).order_by("package_number"))
        if len(remote_packages) == 1 and len(local_packages) > 1:
            remote_packages = remote_packages * len(local_packages)
            pdfs = pdfs * len(local_packages)
        if len(local_packages) != len(remote_packages):
            raise ValueError("La cantidad de bultos del proveedor no coincide con WMS360.")

        for index, package in enumerate(local_packages):
            remote = remote_packages[index]
            package.external_package_id = remote["id"]
            package.tracking_number = remote["tracking"]
            package.tracking_url = remote["tracking_url"]
            package.label_format = ShipmentPackage.LABEL_FORMAT_PDF
            package.label_url = remote["label_url"]
            package.label_file.save(f"skydropx_{remote['tracking']}_bulto_{package.package_number}.pdf", ContentFile(pdfs[index]), save=False)
            transition_package(
                entity_id=package.pk,
                company_id=locked.company_id,
                to_state=ShipmentPackage.STATUS_LABEL_READY,
                source=SOURCE_RECONCILIATION,
                reason="Etiqueta recuperada mediante conciliación segura.",
                actor=(actor or locked.purchase_confirmed_by),
                metadata={
                    "operation": "reconcile_carrier_shipment",
                    "provider": provider,
                    "tracking": remote["tracking"],
                },
            )
            package.last_error_message = ""
            package.save(
                update_fields=[
                    "external_package_id",
                    "tracking_number",
                    "tracking_url",
                    "label_format",
                    "label_url",
                    "label_file",
                    "last_error_message",
                    "updated_at",
                ]
            )

        tracking = remote_packages[0]["tracking"]
        locked.external_shipment_id = provider_id or locked.external_shipment_id
        locked.master_tracking_number = tracking
        locked.master_tracking_url = remote_packages[0]["tracking_url"]
        locked.total_cost = actual_cost
        locked.currency = "MXN"
        transition_shipment(
            entity_id=locked.pk,
            company_id=locked.company_id,
            to_state=CarrierShipment.STATUS_LABEL_READY,
            source=SOURCE_RECONCILIATION,
            reason="Guía y etiqueta recuperadas mediante conciliación segura.",
            actor=(actor or locked.purchase_confirmed_by),
            metadata={
                "operation": "reconcile_carrier_shipment",
                "provider": provider,
                "tracking": tracking,
                "provider_shipment_id": provider_id or "",
            },
        )
        locked.generated_at = timezone.now()
        locked.last_error_type = locked.last_error_code = locked.last_error_message = ""
        response_data = dict(locked.response_data or {})
        response_data["provider_reconciliation"] = payload
        locked.response_data = response_data
        locked.save(
            update_fields=[
                "external_shipment_id",
                "master_tracking_number",
                "master_tracking_url",
                "total_cost",
                "currency",
                "generated_at",
                "last_error_type",
                "last_error_code",
                "last_error_message",
                "response_data",
                "updated_at",
            ]
        )

        purchase = (
            ShippingPurchase.objects.select_for_update()
            .filter(
                company_id=locked.company_id,
                carrier_shipment_id=locked.pk,
                status__in=[
                    ShippingPurchase.STATUS_PROVIDER_PENDING,
                    ShippingPurchase.STATUS_RECONCILING,
                    ShippingPurchase.STATUS_MANUAL_REVIEW,
                ],
            )
            .order_by("-generation", "-pk")
            .first()
        )
        if purchase is not None:
            transition_purchase(
                entity_id=purchase.pk,
                company_id=locked.company_id,
                to_state=ShippingPurchase.STATUS_COMPLETED,
                source=SOURCE_RECONCILIATION,
                reason="La guía fue conciliada y recuperada correctamente.",
                actor=(actor or locked.purchase_confirmed_by),
                metadata={
                    "operation": "reconcile_carrier_shipment",
                    "provider": provider,
                    "tracking": tracking,
                    "shipment_id": locked.pk,
                },
            )
            purchase.charge_status = ShippingPurchase.CHARGE_CONFIRMED
            purchase.completed_at = timezone.now()
            purchase.next_retry_at = None
            purchase.lease_expires_at = None
            purchase.last_error_code = ""
            purchase.last_error_message = ""
            purchase.save(
                update_fields=[
                    "charge_status",
                    "completed_at",
                    "next_retry_at",
                    "lease_expires_at",
                    "last_error_code",
                    "last_error_message",
                    "updated_at",
                ]
            )

        ShippingAuditEvent.objects.create(
            company=locked.company,
            shipping_order=locked.shipping_order,
            carrier_shipment=locked,
            actor=(actor or locked.purchase_confirmed_by),
            action="provider_purchase_reconciled",
            detail={
                "provider": provider,
                "tracking_number": tracking,
                "provider_shipment_id": provider_id or "",
                "charged_amount": str(actual_cost),
            },
        )

    return ReconciliationOutcome(True, False, "Guía conciliada correctamente.", tracking, provider_id or "")
