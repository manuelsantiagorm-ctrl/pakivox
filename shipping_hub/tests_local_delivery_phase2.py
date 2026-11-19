from django.test import SimpleTestCase

from shipping_hub.forms import LocalDeliveryForm
from shipping_hub.models import (
    LocalDeliveryAssignment,
    LocalDeliveryRoute,
    ShipmentPackage,
)


class LocalDeliveryPhase2Tests(SimpleTestCase):
    def test_route_states_are_complete(self):
        states = {value for value, _ in LocalDeliveryRoute.STATUS_CHOICES}
        self.assertEqual(
            states, {"planned", "loading", "in_progress", "completed", "cancelled"}
        )

    def test_assignment_states_include_delivery_attempt(self):
        states = {value for value, _ in LocalDeliveryAssignment.STATUS_CHOICES}
        self.assertIn("failed_attempt", states)
        self.assertIn("delivered", states)

    def test_local_form_requires_route_resources(self):
        form = LocalDeliveryForm(data={"carrier_name": "Entrega propia"})
        self.assertFalse(form.is_valid())
        for field in ("driver", "vehicle", "route_name", "scheduled_date"):
            self.assertIn(field, form.errors)

    def test_stop_order_cannot_be_zero(self):
        form = LocalDeliveryForm(data={
            "carrier_name": "Entrega propia", "route_name": "Ruta 1",
            "scheduled_date": "2026-08-01", "stop_order": "0",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("stop_order", form.errors)

    def test_package_supports_print_and_delivery_states(self):
        states = {value for value, _ in ShipmentPackage.STATUS_CHOICES}
        self.assertTrue({"label_ready", "printed", "in_transit", "delivered"} <= states)
