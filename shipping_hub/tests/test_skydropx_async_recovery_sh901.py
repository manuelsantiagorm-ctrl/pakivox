from pathlib import Path

from django.test import SimpleTestCase


class SkydropxAsyncRecoverySH901Tests(SimpleTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.strategy = (
            root / "shipping_hub" / "strategies"
            / "skydropx_strategy.py"
        ).read_text(encoding="utf-8")
        self.recovery = (
            root / "shipping_hub" / "services"
            / "recovery_engine.py"
        ).read_text(encoding="utf-8")
        self.provider_recovery = (
            root / "shipping_hub" / "services"
            / "skydropx_recovery.py"
        ).read_text(encoding="utf-8")
        self.presenter = (
            root / "shipping_hub" / "services"
            / "purchase_ui.py"
        ).read_text(encoding="utf-8")

    def test_parser_accepts_real_package_type(self):
        self.assertIn(
            'not in {"package", "packages"}',
            self.strategy,
        )

    def test_recovery_uses_exact_remote_id(self):
        self.assertIn(
            "provider_shipment_id_hint=str(",
            self.provider_recovery,
        )
        self.assertIn(
            "purchase.provider_purchase_id",
            self.provider_recovery,
        )

    def test_async_window_is_fifteen_minutes(self):
        self.assertIn(
            "ASYNC_RECOVERY_WINDOW_SECONDS = 15 * 60",
            self.recovery,
        )
        self.assertIn(
            "_async_recovery_window_expired(",
            self.recovery,
        )

    def test_recovery_is_read_only_against_provider(self):
        self.assertIn(
            "reconcile_carrier_shipment(",
            self.provider_recovery,
        )
        self.assertNotIn(
            "_client().create_shipment(",
            self.provider_recovery,
        )
        self.assertNotIn(
            "adapter.create_purchase(",
            self.provider_recovery,
        )

    def test_presenter_explains_async_state(self):
        self.assertIn(
            "Guía en proceso con la paquetería",
            self.presenter,
        )
        self.assertIn(
            "15 minutos",
            self.presenter,
        )
        self.assertIn(
            "No vuelvas a comprar",
            self.presenter,
        )
