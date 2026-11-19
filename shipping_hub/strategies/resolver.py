from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError

from shipping_hub.models import (
    CompanyShippingIntegration,
)

from .envia_strategy import EnviaStrategy
from .estafeta_strategy import (
    EstafetaDirectStrategy,
)
from .skydropx_strategy import SkydropxStrategy

STRATEGY_CLASSES = {
    CompanyShippingIntegration.PROVIDER_ENVIA: (EnviaStrategy),
    CompanyShippingIntegration.PROVIDER_ESTAFETA_DIRECT: (EstafetaDirectStrategy),
    CompanyShippingIntegration.PROVIDER_SKYDROPX: SkydropxStrategy,
}


def get_primary_shipping_integration(
    *,
    company,
    environment=None,
    warehouse=None,
):
    """
    Obtiene la integración principal y activa para la empresa actual.

    Si se indica warehouse, primero busca una integración asignada
    específicamente a esa bodega; si no existe, cae al respaldo de
    empresa (warehouse=None).
    """
    if company is None:
        raise ValidationError("No se detectó la empresa actual.")
    selected_environment = environment or getattr(
        settings,
        "SHIPPING_HUB_ENVIRONMENT",
        CompanyShippingIntegration.ENVIRONMENT_PRODUCTION,
    )

    base_query = CompanyShippingIntegration.objects.for_company(company).filter(
        environment=selected_environment,
        is_active=True,
        is_primary=True,
    )

    integration = None
    if warehouse is not None:
        integration = (
            base_query.filter(warehouse=warehouse)
            .order_by("-updated_at", "-id")
            .first()
        )

    if integration is None:
        integration = (
            base_query.filter(warehouse__isnull=True)
            .order_by("-updated_at", "-id")
            .first()
        )

    if integration is None:
        raise ValidationError(
            (
                "La empresa no tiene una integración "
                "logística principal activa para el "
                f"entorno {selected_environment}."
            )
        )
    return integration


def get_shipping_strategy(
    *,
    integration,
    company,
    require_active=True,
):
    """
    Resuelve una estrategia logística sin permitir
    acceso cruzado entre empresas.

    require_active=True:
        Para cotizaciones y generación de guías.

    require_active=False:
        Para probar una integración antes de activarla.
    """

    if integration is None:
        raise ValidationError("No se recibió una integración logística.")

    if company is None:
        raise ValidationError("No se detectó la empresa actual.")

    if integration.company_id != company.id:
        raise ValidationError(
            ("La integración logística no pertenece " "a la empresa actual.")
        )

    if require_active and not integration.is_active:
        raise ValidationError(
            ("La integración logística seleccionada " "está desactivada.")
        )

    strategy_class = STRATEGY_CLASSES.get(integration.provider)

    if strategy_class is None:
        raise ValidationError(
            (
                "El proveedor logístico seleccionado "
                "todavía no tiene una estrategia "
                "implementada en WMS360."
            )
        )

    return strategy_class(
        integration=integration,
    )
