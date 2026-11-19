from pathlib import Path

from django.test import SimpleTestCase


class ManualDeliverySH7FinalContractTests(SimpleTestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]

    def test_service_is_atomic_company_scoped_and_audited(self):
        source = (
            self.root / "services" / "dispatch_flow.py"
        ).read_text(encoding="utf-8-sig")

        self.assertIn(
            "def mark_shipment_delivered_manually(",
            source,
        )
        self.assertIn("@transaction.atomic", source)
        self.assertIn(
            ".get(pk=shipment_id, company_id=company_id)",
            source,
        )
        self.assertIn("ShipmentPackage.objects.select_for_update()", source)
        self.assertIn('action="delivery_closed_manually"', source)
        self.assertIn('"operation_id": str(operation_id)', source)
        self.assertIn('delivered_at=delivered_at', source)

    def test_service_blocks_invalid_packages_and_requires_actor(self):
        source = (
            self.root / "services" / "dispatch_flow.py"
        ).read_text(encoding="utf-8-sig")

        self.assertIn(
            "Se requiere un usuario autenticado",
            source,
        )
        self.assertIn(
            "cancelados o fallidos",
            source,
        )
        self.assertIn(
            "no tiene bultos registrados",
            source,
        )

    def test_view_requires_approval_and_route_exists(self):
        views = (
            self.root / "dispatch_views.py"
        ).read_text(encoding="utf-8-sig")
        urls = (
            self.root / "dispatch_urls.py"
        ).read_text(encoding="utf-8-sig")

        self.assertIn("def mark_delivered(", views)
        self.assertIn(
            "required_level=ACCESS_APPROVE",
            views,
        )
        self.assertIn('name="mark_delivered"', urls)

    def test_template_has_safe_modal_and_double_click_guard(self):
        template = (
            self.root / "templates" / "shipping_hub"
            / "dispatch_center.html"
        ).read_text(encoding="utf-8-sig")

        self.assertIn("Marcar entregado", template)
        self.assertIn('maxlength="500"', template)
        self.assertIn(
            "if (dispatchForm.dataset.submitting === '1')",
            template,
        )
        self.assertIn(
            "dispatchForm.dataset.submitting = '1'",
            template,
        )
        self.assertIn("submitButton.disabled = true", template)
        self.assertIn("confirmButton.disabled = true", template)
        self.assertNotIn("window.prompt(", template)
        self.assertNotIn("window.confirm(", template)
        self.assertNotIn(".progress-track{", template)
