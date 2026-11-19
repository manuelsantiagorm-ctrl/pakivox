from django.core.management.base import BaseCommand

from shipping_hub.services.skydropx import normalize_skydropx_rates


class Command(BaseCommand):
    help = "Verifica SH-3B sin llamar proveedores."

    def handle(self, *args, **options):
        payload = {
            "data": {
                "id": "quotation-test-1",
                "rates": [
                    {
                        "id": "rate-test-1",
                        "attributes": {
                            "success": True,
                            "total": "99.88",
                            "provider_display_name": "Imile",
                            "provider_service_name": "Express Jal",
                            "currency": "MXN",
                            "days": 2,
                        },
                    }
                ],
            }
        }
        rate = normalize_skydropx_rates(payload)[0]
        assert rate["quotation_id"] == "quotation-test-1"
        assert rate["rate_id"] == "rate-test-1"
        assert (
            rate["response_data"]["skydropx_quotation_id"]
            == "quotation-test-1"
        )
        self.stdout.write(
            self.style.SUCCESS(
                "SH-3B QUOTATION_ID SKYDROPX VERIFICADO: OK"
            )
        )
        self.stdout.write(
            "No se llamó a Skydropx y no se compraron guías."
        )
