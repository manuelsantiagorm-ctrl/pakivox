from pathlib import Path

from django.test import SimpleTestCase


class ManualDeliveryEndpointSH72Tests(SimpleTestCase):
    def setUp(self):
        self.template = (
            Path(__file__).resolve().parents[1]
            / "templates"
            / "shipping_hub"
            / "dispatch_center.html"
        )
        self.text = self.template.read_text(encoding="utf-8")

    def test_delivery_modal_is_unique(self):
        self.assertEqual(self.text.count('id="delivery-modal"'), 1)
        self.assertEqual(self.text.count('id="delivery-form"'), 1)
        self.assertEqual(
            self.text.count("document.querySelectorAll('.js-open-delivery')"),
            1,
        )

    def test_delivery_button_uses_delivery_endpoint(self):
        expected = "data-action=\"{% url 'shipping_hub_dispatch:mark_delivered' shipment.id %}\""
        self.assertIn(expected, self.text)

    def test_dispatch_and_delivery_endpoints_are_not_mixed(self):
        dispatch = "{% url 'shipping_hub_dispatch:mark_dispatched' shipment.id %}"
        delivery = "{% url 'shipping_hub_dispatch:mark_delivered' shipment.id %}"
        self.assertIn(dispatch, self.text)
        self.assertIn(delivery, self.text)

        start = self.text.index('class="js-open-delivery"')
        end = self.text.index("</button>", start)
        delivery_button = self.text[start:end]
        self.assertIn("mark_delivered", delivery_button)
        self.assertNotIn("mark_dispatched", delivery_button)
