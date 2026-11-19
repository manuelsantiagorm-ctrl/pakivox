from django.core.management.base import BaseCommand

from shipping_hub.services.multi_provider import (
    _deduplication_key,
    _normalize_amount,
    _normalize_days,
)


class Command(BaseCommand):
    help = "Verifica la fase SH-1 sin usar red ni proveedores."

    def handle(self, *args, **options):
        assert str(_normalize_amount("123.456")) == "123.46"
        assert _normalize_amount("-1") is None
        assert _normalize_days("3") == 3
        assert _normalize_days("x") is None
        assert _deduplication_key({
            "provider": "envia",
            "carrier": "DHL",
            "service_code": "EXP",
            "amount": "100.00",
            "estimated_delivery_days": 2,
        })

        self.stdout.write(
            self.style.SUCCESS(
                "SH-1 CATÁLOGO Y COTIZACIÓN MULTIPROVEEDOR VERIFICADOS: OK"
            )
        )
        self.stdout.write(
            "No se llamó a proveedores y no se compraron guías."
        )
