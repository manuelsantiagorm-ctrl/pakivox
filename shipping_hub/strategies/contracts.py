from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

PurchaseOutcome = Literal["completed", "pending", "uncertain", "failed_final"]
RetryDisposition = Literal["retry_safe", "reconcile_only", "final_failure", "manual_review"]


@dataclass(frozen=True)
class ProviderLabel:
    tracking_number: str
    label_url: str = ""
    label_content: bytes | None = None
    label_format: str = "pdf"
    external_package_id: str = ""


@dataclass(frozen=True)
class ProviderPurchaseResult:
    outcome: PurchaseOutcome
    retry_disposition: RetryDisposition
    provider_request_id: str = ""
    provider_purchase_id: str = ""
    provider_reference: str = ""
    tracking_numbers: tuple[str, ...] = ()
    labels: tuple[ProviderLabel, ...] = ()
    charged_amount: Decimal | None = None
    currency: str = "MXN"
    error_code: str = ""
    error_message: str = ""
    raw_response: dict[str, Any] = field(default_factory=dict)


class ShippingProviderAdapterV2(ABC):
    """Contrato obligatorio para todos los proveedores del Shipping Core."""

    def __init__(self, *, integration):
        self.integration = integration

    @abstractmethod
    def create_purchase(self, *, purchase, shipment, quote, origin, destination, packages) -> ProviderPurchaseResult:
        raise NotImplementedError

    @abstractmethod
    def get_purchase(self, *, purchase) -> ProviderPurchaseResult:
        raise NotImplementedError

    @abstractmethod
    def find_purchase(self, *, purchase) -> ProviderPurchaseResult:
        raise NotImplementedError

    @abstractmethod
    def download_labels(self, *, purchase, result: ProviderPurchaseResult) -> ProviderPurchaseResult:
        raise NotImplementedError

    def cancel_purchase(self, *, purchase) -> ProviderPurchaseResult:
        return ProviderPurchaseResult(
            outcome="failed_final",
            retry_disposition="manual_review",
            error_code="cancel_not_implemented",
            error_message="El proveedor no implementa cancelación.",
        )
