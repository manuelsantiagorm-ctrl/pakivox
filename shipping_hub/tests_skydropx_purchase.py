from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from shipping_hub.strategies.skydropx_strategy import SkydropxStrategy


@override_settings(
    SHIPPING_HUB_SKYDROPX_CONSIGNMENT_NOTE="53102400",
    SHIPPING_HUB_SKYDROPX_PACKAGE_TYPE="4G",
)
class SkydropxPurchaseContractTests(SimpleTestCase):
    def setUp(self):
        self.integration = SimpleNamespace(
            preferred_label_format="pdf",
            environment="production",
            company=SimpleNamespace(pk=7),
        )
        self.strategy = SkydropxStrategy(integration=self.integration)
        self.quote = SimpleNamespace(
            external_rate_id="rate-123",
            amount=Decimal("52.47"),
            currency="MXN",
            response_data={"skydropx_quotation_id": "quotation-456"},
        )
        self.address = {
            "complete_street": "Av. Reforma 100",
            "name": "Cliente prueba",
            "company_name": "Empresa prueba",
            "phone": "5512345678",
            "email": "prueba@example.com",
            "reference": "Puerta azul",
        }
        self.packages = [SimpleNamespace(pk=1), SimpleNamespace(pk=2)]

    def test_builds_pro_payload_and_returns_view_contract(self):
        client = Mock()
        client.create_shipment.return_value = {
            "data": [{
                "id": "shipment-1",
                "status": "success",
                "master_tracking_number": "TRACK123",
                "label_url": "https://labels.skydropx.com/guide.pdf",
            }]
        }
        with patch.object(self.strategy, "_client", return_value=client):
            result = self.strategy.generate_label(
                shipment=SimpleNamespace(idempotency_key="local-key"),
                selected_quote=self.quote,
                origin=self.address,
                destination=self.address,
                packages=self.packages,
                order_total=Decimal("1000.00"),
            )

        payload = client.create_shipment.call_args.args[0]["shipment"]
        self.assertEqual(payload["quotation_id"], "quotation-456")
        self.assertEqual(payload["rate_id"], "rate-123")
        self.assertTrue(payload["unique_shipment"])
        self.assertEqual(len(payload["packages"]), 2)
        self.assertEqual(result["shipment_id"], "shipment-1")
        self.assertEqual(result["master_tracking_number"], "TRACK123")
        self.assertEqual(result["total_price"], Decimal("52.47"))
        self.assertEqual(len(result["provider_packages"]), 2)

    def test_v2_multishipment_keeps_every_tracking(self):
        client = Mock()
        client.create_shipment.return_value = {
            "data": [
                {"id": "s1", "master_tracking_number": "T1", "label_url": "https://labels.skydropx.com/1.pdf"},
                {"id": "s2", "master_tracking_number": "T2", "label_url": "https://labels.skydropx.com/2.pdf"},
            ]
        }
        with patch.object(self.strategy, "_client", return_value=client):
            result = self.strategy.generate_label(
                shipment=SimpleNamespace(), selected_quote=self.quote,
                origin=self.address, destination=self.address,
                packages=self.packages, order_total=Decimal("1000.00"),
            )
        self.assertEqual([p["tracking_number"] for p in result["provider_packages"]], ["T1", "T2"])

