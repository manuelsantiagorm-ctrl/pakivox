from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from shipping_hub.models import CarrierShipment
from shipping_hub.services.dashboard import ACTIVE_STATUSES, PENDING_STATUSES, build_dashboard


class _Related:
    def __init__(self, values=()): self.values = list(values)
    def all(self): return self.values


def _shipment(status, *, cost="0", hours_old=1, carrier="DHL"):
    now = timezone.now()
    return SimpleNamespace(
        pk=1, status=status, total_cost=cost, purchase_quote_amount=None,
        selected_quote_id=None, carrier_name=carrier, get_provider_display=lambda: "Manual",
        get_status_display=lambda: status, created_at=now-timedelta(hours=hours_old),
        updated_at=now-timedelta(hours=hours_old), generated_at=now-timedelta(hours=hours_old),
        tracking_events=_Related(), delivery_evidences=_Related(), logistics_incidents=_Related(),
        returns=_Related(), delivery_retries=_Related(), reconciliations=_Related(),
    )


class LogisticsDashboardPhase10Tests(SimpleTestCase):
    def test_active_statuses_cover_transit(self): self.assertTrue({"shipped", "in_transit", "out_for_delivery"}.issubset(ACTIVE_STATUSES))
    def test_pending_statuses_cover_ready(self): self.assertIn("ready", PENDING_STATUSES)
    def test_empty_dashboard_has_safe_zero_rates(self):
        data = build_dashboard([]); self.assertEqual(data["success_rate"], 0); self.assertEqual(data["on_time_rate"], 0)
    def test_success_rate_uses_all_shipments(self):
        data = build_dashboard([_shipment(CarrierShipment.STATUS_DELIVERED), _shipment(CarrierShipment.STATUS_IN_TRANSIT)])
        self.assertEqual(data["success_rate"], 50.0)
    def test_stale_excludes_delivered(self):
        data = build_dashboard([_shipment(CarrierShipment.STATUS_DELIVERED, hours_old=80)])
        self.assertEqual(data["stale_shipments"], [])
    def test_stale_detects_open_shipment_after_48_hours(self):
        data = build_dashboard([_shipment(CarrierShipment.STATUS_IN_TRANSIT, hours_old=50)])
        self.assertEqual(len(data["stale_shipments"]), 1)
    def test_carrier_performance_is_grouped(self):
        data = build_dashboard([_shipment(CarrierShipment.STATUS_DELIVERED), _shipment(CarrierShipment.STATUS_IN_TRANSIT)])
        self.assertEqual(data["carrier_rows"][0]["shipments"], 2)
    def test_actual_cost_is_accumulated(self):
        data = build_dashboard([_shipment(CarrierShipment.STATUS_IN_TRANSIT, cost="10.50"), _shipment(CarrierShipment.STATUS_DELIVERED, cost="4.50")])
        self.assertEqual(str(data["actual_cost"]), "15.00")
    def test_dashboard_template_has_chart(self): self.assertIn("statusChart", self._template())
    def test_dashboard_template_has_period_filters(self):
        text = self._template(); self.assertIn('name="start"', text); self.assertIn('name="end"', text)
    def test_dashboard_template_has_export(self): self.assertIn("logistics_dashboard_export", self._template())
    def test_dashboard_template_has_stale_alerts(self): self.assertIn("Envíos estancados", self._template())
    def test_urls_require_specific_warehouse(self):
        text = (Path(__file__).resolve().parent / "urls.py").read_text(encoding="utf-8")
        self.assertIn('"tablero/"', text); self.assertIn("require_specific_warehouse=True", text)
    def test_export_uses_same_authorized_queryset(self):
        text = (Path(__file__).resolve().parent / "views.py").read_text(encoding="utf-8")
        block = text[text.index("def logistics_dashboard_export"):text.index("def _tracking_shipments")]
        self.assertIn("dashboard_shipments(company, warehouse, start, end)", block)
    def test_phase10_dashboard_added_no_schema_migration(self):
        migrations = Path(__file__).resolve().parent / "migrations"
        names = {
            path.name.lower()
            for path in migrations.glob("*.py")
        }

        self.assertFalse(
            any("phase10" in name for name in names)
        )
        self.assertIn(
            "0015_logistics_incidents_phase9.py",
            names,
        )
        self.assertIn(
            "0016_shipping_purchase_core_phase2.py",
            names,
        )

    @staticmethod
    def _template():
        return (Path(__file__).resolve().parent.parent / "templates" / "shipping_hub" / "logistics_dashboard.html").read_text(encoding="utf-8")
