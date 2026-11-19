from pathlib import Path

from django.db.models import PROTECT
from django.test import SimpleTestCase

from shipping_hub.models import DeliveryRetry, LogisticsIncident, LogisticsIncidentEvent, ShipmentReturn, ShippingReconciliation
from shipping_hub.services.incidents import transition_incident


class LogisticsIncidentsPhase9Tests(SimpleTestCase):
    def test_incident_protects_shipment_history(self):
        self.assertIs(LogisticsIncident._meta.get_field("carrier_shipment").remote_field.on_delete, PROTECT)

    def test_incident_protects_warehouse_history(self):
        self.assertIs(LogisticsIncident._meta.get_field("warehouse").remote_field.on_delete, PROTECT)

    def test_incident_idempotency_is_unique(self):
        self.assertTrue(LogisticsIncident._meta.get_field("idempotency_key").unique)

    def test_incident_types_cover_operational_exceptions(self):
        self.assertTrue({"lost", "damaged", "held", "failed_delivery"}.issubset({v for v, _ in LogisticsIncident.TYPE_CHOICES}))

    def test_terminal_statuses_exist(self):
        self.assertTrue({"resolved", "cancelled"}.issubset({v for v, _ in LogisticsIncident.STATUS_CHOICES}))

    def test_history_has_server_timestamp(self):
        self.assertTrue(LogisticsIncidentEvent._meta.get_field("created_at").auto_now_add)

    def test_partial_and_total_returns_exist(self):
        self.assertEqual({v for v, _ in ShipmentReturn.TYPE_CHOICES}, {"total", "partial"})

    def test_return_protects_shipment_history(self):
        self.assertIs(ShipmentReturn._meta.get_field("carrier_shipment").remote_field.on_delete, PROTECT)

    def test_retry_attempt_is_unique_per_shipment(self):
        self.assertIn("uniq_delivery_retry_attempt", {c.name for c in DeliveryRetry._meta.constraints})

    def test_retry_requires_incident_history(self):
        self.assertIs(DeliveryRetry._meta.get_field("incident").remote_field.on_delete, PROTECT)

    def test_reconciliation_keeps_both_costs(self):
        fields = {f.name for f in ShippingReconciliation._meta.fields}
        self.assertTrue({"expected_cost", "provider_cost", "differences"}.issubset(fields))

    def test_reconciliation_has_mismatch_status(self):
        self.assertIn("mismatch", {v for v, _ in ShippingReconciliation.STATUS_CHOICES})

    def test_tracking_detail_exposes_phase9_actions(self):
        text = self._template("tracking_detail.html")
        self.assertIn("incident_create", text); self.assertIn("reconciliation_create", text)

    def test_incident_detail_forms_use_csrf(self):
        text = self._template("incident_detail.html")
        self.assertGreaterEqual(text.count("{% csrf_token %}"), 3)

    def test_center_contains_filters_and_reconciliation(self):
        text = self._template("incident_center.html")
        self.assertIn('name="status"', text); self.assertIn("conciliaciones", text.lower())

    @staticmethod
    def _template(name):
        return (Path(__file__).resolve().parent.parent / "templates" / "shipping_hub" / name).read_text(encoding="utf-8")
