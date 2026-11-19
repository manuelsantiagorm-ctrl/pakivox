from __future__ import annotations

from decimal import Decimal, InvalidOperation
from time import monotonic

from django.conf import settings

import sentry_sdk

from shipping_hub.models import CompanyShippingIntegration
from shipping_hub.strategies.base import ShippingProviderError
from shipping_hub.strategies.resolver import get_shipping_strategy


def get_quote_integrations(*, company, environment=None, warehouse=None):
    if company is None:
        return CompanyShippingIntegration.objects.none()
    selected_environment = environment or getattr(
        settings,
        "SHIPPING_HUB_ENVIRONMENT",
        "production",
    )
    base_query = CompanyShippingIntegration.objects.for_company(company).filter(
        environment=selected_environment,
        is_active=True,
    )

    if warehouse is not None:
        own = base_query.filter(warehouse=warehouse)
        if own.exists():
            return own.order_by("priority", "provider", "id")

    return base_query.filter(warehouse__isnull=True).order_by(
        "priority", "provider", "id"
    )


def _normalize_amount(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None

    if amount < 0:
        return None

    return amount.quantize(Decimal("0.01"))


def _normalize_days(value):
    if value in (None, ""):
        return None

    try:
        days = int(value)
    except (TypeError, ValueError):
        return None

    return days if days >= 0 else None


def _normalize_rate(*, rate, integration):
    if not isinstance(rate, dict):
        return None

    amount = _normalize_amount(rate.get("amount"))
    if amount is None:
        return None

    carrier = str(
        rate.get("carrier")
        or rate.get("carrier_name")
        or rate.get("provider_service")
        or ""
    ).strip()
    service = str(
        rate.get("service")
        or rate.get("service_name")
        or rate.get("service_code")
        or ""
    ).strip()
    service_code = str(
        rate.get("service_code")
        or rate.get("service_id")
        or service
    ).strip()

    normalized = dict(rate)
    normalized.update(
        {
            "amount": amount,
            "estimated_delivery_days": _normalize_days(
                rate.get("estimated_delivery_days")
            ),
            "carrier": carrier,
            "service": service,
            "service_code": service_code,
            "provider": integration.provider,
            "integration_id": integration.id,
        }
    )
    return normalized


def _deduplication_key(rate):
    return (
        str(rate.get("provider") or "").casefold(),
        str(rate.get("carrier") or "").casefold(),
        str(rate.get("service_code") or rate.get("service") or "").casefold(),
        str(rate.get("amount") or ""),
        rate.get("estimated_delivery_days"),
    )


def quote_all_providers(
    *,
    company,
    origin,
    destination,
    packages,
    order_total,
    environment=None,
    warehouse=None,
):
    if company is None:
        raise ShippingProviderError(
            "No se detectó la empresa actual.",
            "company_required",
        )

    rates = []
    errors = []
    attempted = []
    timings = []
    seen_rates = set()

    integrations = list(
        get_quote_integrations(
            company=company,
            environment=environment,
            warehouse=warehouse,
        )
    )

    max_integrations = int(
        getattr(settings, "SHIPPING_HUB_MAX_QUOTE_INTEGRATIONS", 20)
    )
    if max_integrations < 1:
        max_integrations = 1
    integrations = integrations[:max_integrations]

    if not integrations:
        raise ShippingProviderError(
            "No hay agregadores activos para cotizar.",
            "no_active_provider",
        )

    for integration in integrations:
        attempted.append(integration.provider)
        started_at = monotonic()
        provider_rate_count = 0

        try:
            strategy = get_shipping_strategy(
                integration=integration,
                company=company,
            )
            result = strategy.quote(
                origin=origin,
                destination=destination,
                packages=packages,
                order_total=order_total,
            )

            raw_rates = result.get("rates", []) if isinstance(result, dict) else []
            for raw_rate in raw_rates:
                normalized = _normalize_rate(
                    rate=raw_rate,
                    integration=integration,
                )
                if normalized is None:
                    continue

                key = _deduplication_key(normalized)
                if key in seen_rates:
                    continue

                seen_rates.add(key)
                rates.append(normalized)
                provider_rate_count += 1

        except ShippingProviderError as error:
            errors.append(
                {
                    "provider": integration.provider,
                    "integration_id": integration.id,
                    "message": str(error),
                    "error_code": error.error_code,
                    "temporary": error.is_temporary,
                }
            )
            if not integration.allow_quote_fallback:
                raise

        except Exception as error:
            sentry_sdk.capture_exception(error)
            errors.append(
                {
                    "provider": integration.provider,
                    "integration_id": integration.id,
                    "message": (
                        "El proveedor produjo un error interno durante la cotización."
                    ),
                    "error_code": "unexpected_provider_error",
                    "temporary": True,
                    "exception_type": type(error).__name__,
                }
            )
            if not integration.allow_quote_fallback:
                raise ShippingProviderError(
                    "El proveedor produjo un error interno durante la cotización.",
                    "unexpected_provider_error",
                    is_temporary=True,
                ) from error

        finally:
            timings.append(
                {
                    "provider": integration.provider,
                    "integration_id": integration.id,
                    "elapsed_ms": round((monotonic() - started_at) * 1000, 2),
                    "rates": provider_rate_count,
                }
            )

    if not rates:
        details = []
        for error in errors:
            provider = str(
                error.get("provider") or "proveedor"
            ).strip().title()
            message = str(
                error.get("message") or "Error desconocido."
            ).strip()
            code = str(error.get("error_code") or "").strip()
            detail = f"{provider}: {message}"
            if code:
                detail += f" [{code}]"
            details.append(detail)

        if details:
            raise ShippingProviderError(
                "No fue posible obtener tarifas. " + " | ".join(details),
                "all_providers_failed",
            )

        raise ShippingProviderError(
            "Los agregadores respondieron sin tarifas disponibles para este envío.",
            "no_rates_returned",
        )

    rates.sort(
        key=lambda row: (
            row.get("amount"),
            row.get("estimated_delivery_days") or 9999,
            str(row.get("provider") or ""),
        )
    )

    return {
        "rates": rates,
        "errors": errors,
        "providers_attempted": attempted,
        "provider_timings": timings,
        "summary": {
            "providers_attempted": len(attempted),
            "providers_failed": len(errors),
            "rates_returned": len(rates),
        },
    }
