from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from shipping_hub.models import (
    CarrierShipment,
    CompanyShippingIntegration,
    ShippingAuditEvent,
    ShippingQuote,
)


QUOTE_TTL_MINUTES = 15


def _decimal_amount(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValidationError(
            "La tarifa contiene un importe inválido."
        ) from error

    if amount < 0:
        raise ValidationError(
            "La tarifa contiene un importe negativo."
        )
    return amount


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def build_quote_integrity_payload(
    *,
    company_id: int,
    shipping_order_id: int,
    integration_id: int,
    provider: str,
    carrier_code: str,
    carrier_name: str,
    service_code: str,
    service_name: str,
    amount: Decimal,
    currency: str,
    estimated_delivery_days: int | None,
) -> dict[str, Any]:
    return {
        "company_id": int(company_id),
        "shipping_order_id": int(shipping_order_id),
        "integration_id": int(integration_id),
        "provider": str(provider).strip().lower(),
        "carrier_code": str(carrier_code).strip(),
        "carrier_name": str(carrier_name).strip(),
        "service_code": str(service_code).strip(),
        "service_name": str(service_name).strip(),
        "amount": str(amount.quantize(Decimal("0.01"))),
        "currency": str(currency or "MXN").strip().upper(),
        "estimated_delivery_days": estimated_delivery_days,
    }


def quote_integrity_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def select_and_freeze_rate(
    *,
    company,
    cubication,
    rate: dict[str, Any],
    actor=None,
    ttl_minutes: int = QUOTE_TTL_MINUTES,
) -> ShippingQuote:
    if company is None:
        raise ValidationError("No se detectó la empresa.")

    integration_id = rate.get("integration_id")
    if not integration_id:
        raise ValidationError(
            "La tarifa no identifica la integración que la originó."
        )

    provider = str(rate.get("provider") or "").strip().lower()
    if not provider:
        raise ValidationError(
            "La tarifa no identifica proveedor."
        )

    amount = _decimal_amount(rate.get("amount"))
    currency = str(rate.get("currency") or "MXN").strip().upper()
    if not currency:
        currency = "MXN"

    carrier_name = str(
        rate.get("carrier")
        or rate.get("carrier_name")
        or rate.get("carrier_code")
        or "Transportista"
    ).strip()
    carrier_code = str(
        rate.get("carrier_code")
        or rate.get("carrier")
        or carrier_name
    ).strip()
    service_name = str(
        rate.get("service")
        or rate.get("service_name")
        or rate.get("service_code")
        or "Servicio"
    ).strip()
    service_code = str(
        rate.get("service_code")
        or rate.get("service_id")
        or service_name
    ).strip()
    external_rate_id = str(
        rate.get("rate_id")
        or rate.get("id")
        or rate.get("external_rate_id")
        or ""
    ).strip()

    provider_response_data = rate.get("response_data")
    if not isinstance(provider_response_data, dict):
        provider_response_data = {}

    quotation_id = str(
        rate.get("quotation_id")
        or provider_response_data.get("skydropx_quotation_id")
        or provider_response_data.get("quotation_id")
        or ""
    ).strip()

    if provider == "skydropx":
        if not external_rate_id:
            raise ValidationError(
                "La tarifa Skydropx no contiene rate_id."
            )
        if not quotation_id:
            raise ValidationError(
                "La tarifa Skydropx no contiene quotation_id. "
                "Vuelve a cotizar antes de preparar la compra."
            )

    days = rate.get("estimated_delivery_days")
    if days not in (None, ""):
        try:
            days = int(days)
        except (TypeError, ValueError) as error:
            raise ValidationError(
                "La tarifa contiene días estimados inválidos."
            ) from error
        if days < 0:
            raise ValidationError(
                "La tarifa contiene días estimados negativos."
            )
    else:
        days = None

    expires_at = timezone.now() + timedelta(
        minutes=max(1, int(ttl_minutes))
    )

    shipping_order_id = cubication.shipping_order.pk
    order_id = cubication.order.pk
    shipment_id = cubication.carrier_shipment.pk

    with transaction.atomic():
        integration = (
            CompanyShippingIntegration.objects.select_for_update()
            .get(
                pk=integration_id,
                company=company,
                provider=provider,
                is_active=True,
            )
        )

        shipment = (
            CarrierShipment.objects.select_for_update()
            .select_related("shipping_order", "shipping_order__order")
            .get(
                pk=shipment_id,
                company=company,
                shipping_order_id=shipping_order_id,
            )
        )

        if shipment.status != CarrierShipment.STATUS_DRAFT:
            raise ValidationError(
                "La tarifa solo puede seleccionarse en un envío borrador."
            )
        if shipment.master_tracking_number or shipment.has_label:
            raise ValidationError(
                "El envío ya tiene tracking o etiqueta."
            )
        if shipment.shipping_order.order_id != order_id:
            raise ValidationError(
                "El pedido no corresponde al envío."
            )

        payload = build_quote_integrity_payload(
            company_id=company.id,
            shipping_order_id=shipping_order_id,
            integration_id=integration.id,
            provider=provider,
            carrier_code=carrier_code,
            carrier_name=carrier_name,
            service_code=service_code,
            service_name=service_name,
            amount=amount,
            currency=currency,
            estimated_delivery_days=days,
        )
        integrity_hash = quote_integrity_hash(payload)

        ShippingQuote.objects.filter(
            company=company,
            order_id=order_id,
            is_selected=True,
        ).update(is_selected=False)

        response_data = {
            **_json_safe(provider_response_data),
            "raw_rate": _json_safe(rate),
            "quotation_id": quotation_id,
            "skydropx_quotation_id": quotation_id,
            "rate_id": external_rate_id,
            "integrity": payload,
            "integrity_sha256": integrity_hash,
            "selected_at": timezone.now().isoformat(),
            "selected_by_id": getattr(actor, "pk", None),
        }

        try:
            quote = ShippingQuote.objects.create(
                company=company,
                order_id=order_id,
                shipping_order_id=shipping_order_id,
                provider=provider,
                integration=integration,
                external_rate_id=external_rate_id,
                carrier_code=carrier_code[:100],
                carrier_name=carrier_name[:150],
                service_code=service_code[:100],
                service_name=service_name[:150],
                amount=amount,
                currency=currency[:10],
                estimated_delivery_days=days,
                estimated_delivery_text=str(
                    rate.get("estimated_delivery_text") or ""
                )[:150],
                is_selected=True,
                expires_at=expires_at,
                response_data=response_data,
            )
        except IntegrityError as error:
            raise ValidationError(
                "Otra tarifa fue seleccionada al mismo tiempo. "
                "Actualiza e inténtalo nuevamente."
            ) from error

        shipment.selected_quote = quote
        shipment.provider = provider
        shipment.carrier_code = carrier_code[:100]
        shipment.carrier_name = carrier_name[:150]
        shipment.service_code = service_code[:100]
        shipment.service_name = service_name[:150]
        shipment.total_cost = amount
        shipment.save(
            update_fields=[
                "selected_quote",
                "provider",
                "carrier_code",
                "carrier_name",
                "service_code",
                "service_name",
                "total_cost",
                "updated_at",
            ]
        )
    ShippingAuditEvent.objects.create(
        company=company,
        shipping_order_id=shipping_order_id,
        carrier_shipment=shipment,
        actor=actor,
        action="quote_selected",
        detail={
            "message": ("Tarifa seleccionada y congelada para preparar compra."),
            "provider": provider,
            "quote_id": quote.id,
            "integration_id": integration.id,
            "amount": str(amount),
            "currency": currency,
            "integrity_sha256": integrity_hash,
            "expires_at": expires_at.isoformat(),
            "actor_id": getattr(actor, "pk", None),
        },
    )

    return quote


def validate_frozen_quote(*, quote: ShippingQuote) -> str:
    if quote.expires_at and quote.expires_at <= timezone.now():
        raise ValidationError(
            "La cotización seleccionada ya expiró."
        )

    data = quote.response_data or {}
    payload = data.get("integrity")
    expected = data.get("integrity_sha256")
    if not isinstance(payload, dict) or not expected:
        raise ValidationError(
            "La cotización no contiene firma de integridad."
        )

    actual = quote_integrity_hash(payload)
    if actual != expected:
        raise ValidationError(
            "La integridad de la cotización fue alterada."
        )

    current_payload = build_quote_integrity_payload(
        company_id=quote.company_id,
        shipping_order_id=quote.shipping_order_id,
        integration_id=quote.integration_id,
        provider=quote.provider,
        carrier_code=quote.carrier_code,
        carrier_name=quote.carrier_name,
        service_code=quote.service_code,
        service_name=quote.service_name,
        amount=quote.amount,
        currency=quote.currency,
        estimated_delivery_days=quote.estimated_delivery_days,
    )
    current_hash = quote_integrity_hash(current_payload)
    if current_hash != expected:
        raise ValidationError(
            "Los datos persistidos de la tarifa fueron modificados."
        )

    return expected


def freeze_existing_quote(
    *,
    company,
    cubication,
    quote_id: int,
    actor=None,
) -> ShippingQuote:
    """
    Selecciona y firma una cotización ya persistida por la UI.

    Mantiene una única implementación de integridad para:
    - interfaz web;
    - worker durable;
    - validación previa a compra.

    No crea una compra ni llama al proveedor.
    """
    if company is None:
        raise ValidationError("No se detectó la empresa.")

    shipping_order_id = cubication.shipping_order.pk
    order_id = cubication.order.pk
    shipment_id = cubication.carrier_shipment.pk

    with transaction.atomic():
        quote = (
            ShippingQuote.objects.select_for_update()
            .get(
                pk=quote_id,
                company=company,
                order_id=order_id,
                shipping_order_id=shipping_order_id,
            )
        )

        if not quote.integration_id:
            raise ValidationError(
                "La cotización no identifica la integración que la originó."
            )

        integration = (
            CompanyShippingIntegration.objects.select_for_update()
            .get(
                pk=quote.integration_id,
                company=company,
                provider=quote.provider,
                is_active=True,
            )
        )

        shipment = (
            CarrierShipment.objects.select_for_update()
            .get(
                pk=shipment_id,
                company=company,
                shipping_order_id=shipping_order_id,
            )
        )

        if shipment.status != CarrierShipment.STATUS_DRAFT:
            raise ValidationError(
                "La tarifa solo puede seleccionarse en un envío borrador."
            )

        if shipment.master_tracking_number or shipment.has_label:
            raise ValidationError(
                "El envío ya tiene tracking o etiqueta."
            )

        if shipment.shipping_order_id != shipping_order_id:
            raise ValidationError(
                "La cotización y el envío no corresponden a la misma orden."
            )

        if quote.expires_at and quote.expires_at <= timezone.now():
            raise ValidationError(
                "La cotización seleccionada ya expiró. Vuelve a cotizar."
            )

        response_data = dict(quote.response_data or {})
        raw_rate = response_data.get("raw_rate")
        if not isinstance(raw_rate, dict):
            raw_rate = {}

        external_rate_id = str(
            quote.external_rate_id
            or response_data.get("rate_id")
            or raw_rate.get("rate_id")
            or raw_rate.get("id")
            or ""
        ).strip()

        quotation_id = str(
            response_data.get("quotation_id")
            or response_data.get("skydropx_quotation_id")
            or raw_rate.get("quotation_id")
            or (
                raw_rate.get("response_data", {})
                if isinstance(raw_rate.get("response_data"), dict)
                else {}
            ).get("quotation_id")
            or ""
        ).strip()

        if quote.provider == "skydropx":
            if not external_rate_id:
                raise ValidationError(
                    "La tarifa Skydropx no contiene rate_id."
                )
            if not quotation_id:
                raise ValidationError(
                    "La tarifa Skydropx no contiene quotation_id. "
                    "Vuelve a cotizar antes de seleccionarla."
                )

        payload = build_quote_integrity_payload(
            company_id=company.id,
            shipping_order_id=shipping_order_id,
            integration_id=integration.id,
            provider=quote.provider,
            carrier_code=quote.carrier_code,
            carrier_name=quote.carrier_name,
            service_code=quote.service_code,
            service_name=quote.service_name,
            amount=_decimal_amount(quote.amount),
            currency=quote.currency,
            estimated_delivery_days=quote.estimated_delivery_days,
        )
        integrity_hash = quote_integrity_hash(payload)

        ShippingQuote.objects.filter(
            company=company,
            order_id=order_id,
            is_selected=True,
        ).exclude(pk=quote.pk).update(is_selected=False)

        response_data.update(
            {
                "rate_id": external_rate_id,
                "quotation_id": quotation_id,
                "skydropx_quotation_id": quotation_id,
                "integrity": payload,
                "integrity_sha256": integrity_hash,
                "selected_at": timezone.now().isoformat(),
                "selected_by_id": getattr(actor, "pk", None),
            }
        )

        quote.is_selected = True
        quote.response_data = _json_safe(response_data)
        quote.full_clean()
        quote.save(
            update_fields=[
                "is_selected",
                "response_data",
                "updated_at",
            ]
        )

        shipment.selected_quote = quote
        shipment.provider = quote.provider
        shipment.carrier_code = quote.carrier_code
        shipment.carrier_name = quote.carrier_name
        shipment.service_code = quote.service_code
        shipment.service_name = quote.service_name
        shipment.total_cost = quote.amount
        shipment.currency = quote.currency

        shipment_response = dict(shipment.response_data or {})
        shipment_response["selected_quote"] = {
            "quote_id": quote.id,
            "external_rate_id": external_rate_id,
            "quotation_id": quotation_id,
            "carrier_code": quote.carrier_code,
            "carrier_name": quote.carrier_name,
            "service_code": quote.service_code,
            "service_name": quote.service_name,
            "amount": str(quote.amount),
            "currency": quote.currency,
            "estimated_delivery_days": quote.estimated_delivery_days,
            "estimated_delivery_text": quote.estimated_delivery_text,
            "integrity_sha256": integrity_hash,
            "selected_by_user_id": getattr(actor, "pk", None),
            "selected_at": timezone.now().isoformat(),
        }
        shipment.response_data = _json_safe(shipment_response)
        shipment.last_error_type = ""
        shipment.last_error_code = ""
        shipment.last_error_message = ""
        shipment.full_clean()
        shipment.save(
            update_fields=[
                "selected_quote",
                "provider",
                "carrier_code",
                "carrier_name",
                "service_code",
                "service_name",
                "total_cost",
                "currency",
                "response_data",
                "last_error_type",
                "last_error_code",
                "last_error_message",
                "updated_at",
            ]
        )

        ShippingAuditEvent.objects.create(
            company=company,
            shipping_order_id=shipping_order_id,
            carrier_shipment=shipment,
            actor=actor,
            action="quote_selected",
            detail={
                "message": (
                    "Tarifa existente seleccionada y firmada desde la UI."
                ),
                "provider": quote.provider,
                "quote_id": quote.id,
                "integration_id": integration.id,
                "amount": str(quote.amount),
                "currency": quote.currency,
                "integrity_sha256": integrity_hash,
                "actor_id": getattr(actor, "pk", None),
            },
        )

    return quote
