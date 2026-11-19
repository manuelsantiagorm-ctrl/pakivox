from django.core.management.base import BaseCommand

from shipping_hub.strategies.contracts import ProviderPurchaseResult
from shipping_hub.strategies.purchase_adapter_v2 import (
    normalize_legacy_purchase_result,
    normalize_provider_error,
)
from shipping_hub.strategies.base import ShippingProviderError


class Command(BaseCommand):
    help = "Verifica el contrato universal de compras sin llamar proveedores."

    def handle(self, *args, **options):
        completed = normalize_legacy_purchase_result({
            "shipment_id": "remote-1",
            "master_tracking_number": "TRACK-1",
            "label_url": "https://example.invalid/label.pdf",
            "provider_packages": [{
                "id": "pkg-1",
                "trackingNumber": "TRACK-1",
                "labelUrl": "https://example.invalid/label.pdf",
            }],
            "total_price": "51.19",
            "currency": "MXN",
        })
        assert isinstance(completed, ProviderPurchaseResult)
        assert completed.outcome == "completed"
        assert completed.provider_purchase_id == "remote-1"

        incomplete = normalize_legacy_purchase_result({
            "shipment_id": "remote-2",
            "master_tracking_number": "",
            "label_url": "",
        })
        assert incomplete.outcome == "uncertain"
        assert incomplete.retry_disposition == "reconcile_only"

        timeout = normalize_provider_error(ShippingProviderError(
            "Tiempo agotado",
            error_code="timeout",
            is_temporary=True,
        ))
        assert timeout.outcome == "uncertain"

        final = normalize_provider_error(ShippingProviderError(
            "Dirección inválida",
            error_code="invalid_address",
            status_code=422,
            is_temporary=False,
        ))
        assert final.outcome == "failed_final"

        self.stdout.write(self.style.SUCCESS(
            "FASE 3B CONTRATO UNIVERSAL VERIFICADO: OK"
        ))
        self.stdout.write(
            "No se llamó a SkydropX, Envia.com ni a ningún proveedor."
        )
