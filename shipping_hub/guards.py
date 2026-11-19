from django.conf import settings
from django.core.exceptions import PermissionDenied

from .models import CompanyShippingIntegration


class ShippingOperationBlocked(PermissionDenied):
    """Operación logística bloqueada por seguridad."""


def ensure_quote_allowed(integration):
    """
    Permite cotizar en Sandbox.

    En Producción solamente permite cotizar cuando la configuración
    SHIPPING_HUB_ALLOW_PRODUCTION_QUOTES está habilitada.
    """

    if not integration:
        raise ShippingOperationBlocked(
            "No existe una integración logística configurada."
        )

    if not integration.is_active:
        raise ShippingOperationBlocked("La integración logística está inactiva.")

    if not integration.last_connection_test_success:
        raise ShippingOperationBlocked(
            "La integración no tiene una prueba de conexión válida."
        )

    if (
        integration.environment == CompanyShippingIntegration.ENVIRONMENT_PRODUCTION
        and not settings.SHIPPING_HUB_ALLOW_PRODUCTION_QUOTES
    ):
        raise ShippingOperationBlocked(
            "Las cotizaciones de producción están bloqueadas."
        )


def ensure_label_generation_allowed(integration):
    """
    Solo permite comprar con una cuenta logística propia de la empresa.
    La cuenta maestra de WMS360 es exclusivamente un cotizador.
    """

    if not integration:
        raise ShippingOperationBlocked(
            "No existe una integración logística configurada."
        )

    if not integration.is_active:
        raise ShippingOperationBlocked("La integración logística está inactiva.")

    if not integration.last_connection_test_success:
        raise ShippingOperationBlocked(
            "La integración no tiene una prueba de conexión válida."
        )

    if (
        integration.account_mode
        != CompanyShippingIntegration.ACCOUNT_MODE_COMPANY
    ):
        raise ShippingOperationBlocked(
            "La cuenta maestra de WMS360 solo permite consultar tarifas."
        )

    if not getattr(settings, "SHIPPING_HUB_BYOK_LABELS_ENABLED", True):
        raise ShippingOperationBlocked(
            "La compra mediante API propia está desactivada temporalmente."
        )
