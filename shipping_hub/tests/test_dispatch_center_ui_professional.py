from pathlib import Path

from django.test import SimpleTestCase


class DispatchCenterProfessionalUiTests(SimpleTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.source = (
            root / "templates" / "shipping_hub" / "dispatch_center.html"
        ).read_text(encoding="utf-8-sig")

    def test_keeps_sh72_delivery_dom_contract(self):
        self.assertIn('class="js-open-delivery"', self.source)
        self.assertEqual(
            self.source.count("document.querySelectorAll('.js-open-delivery')"),
            1,
        )
        start = self.source.index('class="js-open-delivery"')
        end = self.source.index("</button>", start)
        delivery_button = self.source[start:end]
        self.assertIn("mark_delivered", delivery_button)
        self.assertNotIn("mark_dispatched", delivery_button)

    def test_keeps_operational_actions_and_csrf(self):
        self.assertIn("Marcar como enviado", self.source)
        self.assertIn("Marcar entregado", self.source)
        self.assertIn("Ver timeline", self.source)
        self.assertIn("{% csrf_token %}", self.source)

    def test_keeps_all_dispatch_stages(self):
        self.assertIn("Por enviar", self.source)
        self.assertIn("En tránsito", self.source)
        self.assertIn("En reparto", self.source)
        self.assertIn("Entregados", self.source)
        self.assertIn("Incidencias", self.source)

    def test_keeps_external_links_hardened(self):
        self.assertIn('rel="noopener noreferrer"', self.source)
        self.assertIn("master_tracking_url", self.source)
        self.assertIn("primary_package.label_url", self.source)

    def test_has_double_submit_protection(self):
        self.assertIn("deliverySubmitting", self.source)
        self.assertIn("dataset.submitting", self.source)
        self.assertIn("submitButton.disabled = true", self.source)

    def test_does_not_restore_removed_logistics_progress_bar(self):
        self.assertNotIn('class="progress-track"', self.source)
        self.assertNotIn("Progreso logístico", self.source)

    def test_has_pagination_and_search(self):
        self.assertIn("page.paginator.count", self.source)
        self.assertIn("page.previous_page_number", self.source)
        self.assertIn("page.next_page_number", self.source)
        self.assertIn('name="q"', self.source)
