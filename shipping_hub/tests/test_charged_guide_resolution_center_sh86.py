from pathlib import Path

from django.test import SimpleTestCase


class ChargedGuideResolutionCenterSH86Tests(SimpleTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.views = (root / "shipping_hub" / "views.py").read_text(encoding="utf-8")
        self.urls = (root / "shipping_hub" / "urls.py").read_text(encoding="utf-8")
        self.service = (root / "shipping_hub" / "services" / "manual_recovery.py").read_text(encoding="utf-8")
        self.reconciliation = (root / "shipping_hub" / "services" / "purchase_reconciliation.py").read_text(encoding="utf-8")
        self.template = (root / "templates" / "shipping_hub" / "cubication.html").read_text(encoding="utf-8")

    def test_endpoint_requires_approve_capability(self):
        self.assertIn('name="shipping_purchase_manual_resolution"', self.urls)
        self.assertIn('required_level=ACCESS_APPROVE', self.urls)

    def test_service_is_tenant_and_shipment_scoped(self):
        self.assertIn("company_id=company.pk", self.service)
        self.assertIn("carrier_shipment_id=carrier_shipment.pk", self.service)
        self.assertIn("ShippingPurchase.objects.select_for_update()", self.service)

    def test_service_never_creates_or_rebuys(self):
        self.assertNotIn("create_shipment(", self.service)
        self.assertNotIn("ShippingPurchase.objects.create", self.service)
        self.assertIn("reconcile_carrier_shipment(", self.service)

    def test_confirmation_and_audit_exist(self):
        self.assertIn('expected_confirmation = f"RECUPERAR {purchase_id}"', self.views)
        self.assertIn("ShippingPurchaseEvent.objects.create", self.service)
        self.assertIn("ShippingAuditEvent.objects.create", self.service)

    def test_exact_remote_id_is_used(self):
        self.assertIn("provider_shipment_id_hint=provider_id", self.service)
        self.assertIn("payload = client.get_shipment(exact_provider_id)", self.reconciliation)

    def test_template_explains_no_second_purchase(self):
        self.assertIn("Resolver una guía ya cobrada", self.template)
        self.assertIn("nunca realiza una", self.template)
        self.assertIn("Recuperar tracking y etiqueta", self.template)
