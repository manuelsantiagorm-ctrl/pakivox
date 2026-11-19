from __future__ import annotations

import sentry_sdk
from decimal import Decimal, InvalidOperation
from typing import Any

from shipping_hub.strategies.base import ShippingProviderError
from shipping_hub.strategies.contracts import (
    ProviderLabel,
    ProviderPurchaseResult,
    ShippingProviderAdapterV2,
)
from shipping_hub.strategies.resolver import get_shipping_strategy
from shipping_hub.models import CompanyShippingIntegration
from shipping_hub.services.skydropx_recovery import recover_skydropx_purchase
from shipping_hub.services.envia_recovery import recover_envia_purchase


UNCERTAIN_CODES = {
    "timeout",
    "connection_error",
    "provider_server_error",
    "incomplete_label",
    "missing_package_tracking",
    "invalid_provider_response",
    "empty_response",
    "unexpected_provider_error",
}


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _safe_raw(value: Any) -> dict:
    return value if isinstance(value, dict) else {"value": str(value)}


def normalize_legacy_purchase_result(result: dict) -> ProviderPurchaseResult:
    """
    Convierte la respuesta histórica de cualquier strategy.generate_label()
    al contrato universal del Shipping Core.
    """
    if not isinstance(result, dict):
        return ProviderPurchaseResult(
            outcome="uncertain",
            retry_disposition="reconcile_only",
            error_code="invalid_provider_response",
            error_message="El proveedor devolvió una respuesta no estructurada.",
            raw_response=_safe_raw(result),
        )

    provider_packages = result.get("provider_packages") or []
    labels: list[ProviderLabel] = []
    tracking_numbers: list[str] = []

    for item in provider_packages:
        if not isinstance(item, dict):
            continue
        tracking = str(
            item.get("trackingNumber")
            or item.get("tracking_number")
            or item.get("tracking")
            or ""
        ).strip()
        label_value = str(
            item.get("label")
            or item.get("labelUrl")
            or item.get("label_url")
            or ""
        ).strip()
        if tracking:
            tracking_numbers.append(tracking)
        labels.append(
            ProviderLabel(
                tracking_number=tracking,
                label_url=label_value if label_value.startswith(("http://", "https://")) else "",
                label_content=None,
                label_format=str(result.get("model_label_format") or "pdf").lower(),
                external_package_id=str(
                    item.get("packageId")
                    or item.get("package_id")
                    or item.get("id")
                    or ""
                ).strip(),
            )
        )

    master_tracking = str(result.get("master_tracking_number") or "").strip()
    master_label = str(result.get("label_url") or result.get("label_value") or "").strip()

    if master_tracking and master_tracking not in tracking_numbers:
        tracking_numbers.insert(0, master_tracking)

    if not labels and (master_tracking or master_label):
        labels.append(
            ProviderLabel(
                tracking_number=master_tracking,
                label_url=master_label if master_label.startswith(("http://", "https://")) else "",
                label_content=None,
                label_format=str(result.get("model_label_format") or "pdf").lower(),
            )
        )

    has_tracking = bool(master_tracking or any(label.tracking_number for label in labels))
    has_label = bool(
        master_label
        or any(label.label_url or label.label_content for label in labels)
    )

    if not has_tracking or not has_label:
        return ProviderPurchaseResult(
            outcome="uncertain",
            retry_disposition="reconcile_only",
            provider_purchase_id=str(
                result.get("shipment_id")
                or result.get("external_shipment_id")
                or ""
            ).strip(),
            provider_reference=str(result.get("provider_reference") or "").strip(),
            tracking_numbers=tuple(tracking_numbers),
            labels=tuple(labels),
            charged_amount=_decimal(result.get("total_price")),
            currency=str(result.get("currency") or "MXN").upper(),
            error_code="incomplete_label",
            error_message=(
                "El proveedor pudo crear o cobrar la guía, pero todavía no "
                "devolvió rastreo y etiqueta completos."
            ),
            raw_response=_safe_raw(result.get("raw_response") or result),
        )

    return ProviderPurchaseResult(
        outcome="completed",
        retry_disposition="retry_safe",
        provider_request_id=str(result.get("provider_request_id") or "").strip(),
        provider_purchase_id=str(
            result.get("shipment_id")
            or result.get("external_shipment_id")
            or ""
        ).strip(),
        provider_reference=str(result.get("provider_reference") or "").strip(),
        tracking_numbers=tuple(tracking_numbers),
        labels=tuple(labels),
        charged_amount=_decimal(result.get("total_price")),
        currency=str(result.get("currency") or "MXN").upper(),
        raw_response=_safe_raw(result.get("raw_response") or result),
    )


def normalize_provider_error(error: Exception) -> ProviderPurchaseResult:
    code = str(getattr(error, "error_code", "") or "provider_error")
    temporary = bool(getattr(error, "is_temporary", False))
    status_code = getattr(error, "status_code", None)

    if temporary or code in UNCERTAIN_CODES or (status_code and int(status_code) >= 500):
        return ProviderPurchaseResult(
            outcome="uncertain",
            retry_disposition="reconcile_only",
            error_code=code,
            error_message=str(error),
        )

    return ProviderPurchaseResult(
        outcome="failed_final",
        retry_disposition="final_failure",
        error_code=code,
        error_message=str(error),
    )


class StrategyPurchaseAdapterV2(ShippingProviderAdapterV2):
    """
    Puente de transición profesional.

    SkydropX, Envia.com y cualquier strategy histórica entran al mismo contrato.
    El PurchaseEngine deja de interpretar diccionarios específicos por proveedor.
    """

    def _strategy(self, *, purchase):
        return get_shipping_strategy(
            integration=self.integration,
            company=purchase.company,
        )

    def create_purchase(
        self,
        *,
        purchase,
        shipment,
        quote,
        origin,
        destination,
        packages,
    ) -> ProviderPurchaseResult:
        try:
            legacy = self._strategy(purchase=purchase).generate_label(
                shipment=shipment,
                selected_quote=quote,
                origin=origin,
                destination=destination,
                packages=packages,
                order_total=Decimal(
                    str((purchase.request_snapshot or {}).get("order_total") or "0")
                ),
            )
            return normalize_legacy_purchase_result(legacy)
        except ShippingProviderError as error:
            return normalize_provider_error(error)
        except Exception as error:
            sentry_sdk.capture_exception(error)
            return ProviderPurchaseResult(
                outcome="uncertain",
                retry_disposition="reconcile_only",
                error_code="unexpected_provider_error",
                error_message=(
                    "La comunicación con el proveedor terminó de forma incierta."
                ),
                raw_response={"exception_type": type(error).__name__},
            )

    def get_purchase(self, *, purchase) -> ProviderPurchaseResult:
        if self.integration.provider == CompanyShippingIntegration.PROVIDER_SKYDROPX:
            try:
                return recover_skydropx_purchase(purchase=purchase)
            except ShippingProviderError as error:
                return normalize_provider_error(error)
            except Exception as error:
                sentry_sdk.capture_exception(error)
                return ProviderPurchaseResult(
                    outcome="uncertain",
                    retry_disposition="reconcile_only",
                    provider_purchase_id=purchase.provider_purchase_id,
                    provider_reference=purchase.provider_reference,
                    error_code="skydropx_recovery_unexpected_error",
                    error_message=(
                        "La consulta de recuperación de SkydropX terminó de "
                        "forma incierta. WMS360 no repetirá la compra."
                    ),
                    raw_response={"exception_type": type(error).__name__},
                )

        if self.integration.provider == CompanyShippingIntegration.PROVIDER_ENVIA:
            try:
                return recover_envia_purchase(purchase=purchase)
            except ShippingProviderError as error:
                return normalize_provider_error(error)
            except Exception as error:
                sentry_sdk.capture_exception(error)
                return ProviderPurchaseResult(
                    outcome="uncertain",
                    retry_disposition="reconcile_only",
                    provider_purchase_id=purchase.provider_purchase_id,
                    provider_reference=purchase.provider_reference,
                    error_code="envia_recovery_unexpected_error",
                    error_message=(
                        "La consulta de recuperación de Envia.com terminó de "
                        "forma incierta. WMS360 no repetirá la compra."
                    ),
                    raw_response={"exception_type": type(error).__name__},
                )

        return ProviderPurchaseResult(
            outcome="pending",
            retry_disposition="reconcile_only",
            provider_purchase_id=purchase.provider_purchase_id,
            provider_reference=purchase.provider_reference,
            error_code="provider_lookup_pending_adapter",
            error_message=(
                "Este proveedor todavía no tiene recuperación remota habilitada."
            ),
        )

    def find_purchase(self, *, purchase) -> ProviderPurchaseResult:
        # En SkydropX, la misma conciliación primero intenta IDs externos y
        # después busca por referencia/idempotencia, sin crear otra guía.
        return self.get_purchase(purchase=purchase)

    def download_labels(
        self,
        *,
        purchase,
        result: ProviderPurchaseResult,
    ) -> ProviderPurchaseResult:
        return result


def get_purchase_adapter_v2(*, integration, company) -> StrategyPurchaseAdapterV2:
    if integration is None or company is None:
        raise ValueError("Se requiere empresa e integración logística.")
    if integration.company_id != company.id:
        raise ValueError("La integración logística pertenece a otra empresa.")
    return StrategyPurchaseAdapterV2(integration=integration)
