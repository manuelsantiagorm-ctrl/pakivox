from __future__ import annotations

from dataclasses import asdict, dataclass

from django.conf import settings

from shipping_hub.models import CompanyShippingIntegration
from shipping_hub.strategies.resolver import STRATEGY_CLASSES


@dataclass(frozen=True)
class ProviderCatalogItem:
    provider: str
    provider_name: str
    integration_id: int | None
    environment: str
    configured: bool
    active: bool
    primary: bool
    strategy_available: bool
    credential_mode: str
    quote_ready: bool
    priority: int | None
    status: str
    status_message: str

    def as_dict(self):
        return asdict(self)


def _provider_choices():
    field = CompanyShippingIntegration._meta.get_field("provider")
    return tuple(field.flatchoices)


def _credential_mode(integration):
    if integration is None:
        return "not_configured"

    for field_name in (
        "credential_source",
        "credentials_source",
        "account_mode",
    ):
        if hasattr(integration, field_name):
            value = str(getattr(integration, field_name) or "").strip()
            if value:
                return value

    if getattr(integration, "uses_master_account", False):
        return "master"

    return "company"


def build_provider_catalog(*, company, environment=None):
    if company is None:
        raise ValueError("No se detectó la empresa actual.")

    selected_environment = environment or getattr(
        settings,
        "SHIPPING_HUB_ENVIRONMENT",
        "production",
    )

    integrations = {
        integration.provider: integration
        for integration in (
            CompanyShippingIntegration.objects.for_company(company)
            .filter(environment=selected_environment)
            .order_by("priority", "id")
        )
    }

    items = []
    for provider, provider_name in _provider_choices():
        integration = integrations.get(provider)
        strategy_available = provider in STRATEGY_CLASSES
        configured = integration is not None
        active = bool(integration and integration.is_active)
        quote_ready = bool(configured and active and strategy_available)

        if not configured:
            status = "not_configured"
            message = "La empresa todavía no configuró este proveedor."
        elif not strategy_available:
            status = "strategy_unavailable"
            message = "El proveedor aún no tiene estrategia activa en WMS360."
        elif not active:
            status = "inactive"
            message = "La integración existe, pero está desactivada."
        else:
            status = "ready"
            message = "Proveedor listo para cotización."

        items.append(
            ProviderCatalogItem(
                provider=str(provider),
                provider_name=str(provider_name),
                integration_id=(integration.id if integration else None),
                environment=selected_environment,
                configured=configured,
                active=active,
                primary=bool(integration and integration.is_primary),
                strategy_available=strategy_available,
                credential_mode=_credential_mode(integration),
                quote_ready=quote_ready,
                priority=(integration.priority if integration else None),
                status=status,
                status_message=message,
            )
        )

    items.sort(
        key=lambda item: (
            not item.quote_ready,
            item.priority if item.priority is not None else 9999,
            item.provider_name.casefold(),
        )
    )
    return items
