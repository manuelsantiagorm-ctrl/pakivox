from pathlib import Path

from django.test import SimpleTestCase


class DispatchEligibilitySH73ContractTests(SimpleTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.flow = (
            root / "shipping_hub" / "services" / "dispatch_flow.py"
        ).read_text(encoding="utf-8")
        self.views = (
            root / "shipping_hub" / "dispatch_views.py"
        ).read_text(encoding="utf-8")

    def test_ready_tab_and_counter_use_single_filter(self):
        self.assertIn(
            "def filter_dispatchable_shipments(queryset):",
            self.flow,
        )
        self.assertIn(
            "queryset = filter_dispatchable_shipments(queryset)",
            self.views,
        )
        self.assertIn(
            '"ready": filter_dispatchable_shipments(counts_source).count()',
            self.views,
        )

    def test_filter_excludes_terminal_unpaid_and_incomplete_orders(self):
        self.assertIn(
            'shipping_order__order__status__in=("cancelled", "delivered")',
            self.flow,
        )
        self.assertIn(
            "shipping_order__order__balance_due__lte=0",
            self.flow,
        )
        self.assertIn(
            "shipping_order__packing_order__status__in=completed_packings",
            self.flow,
        )

    def test_validation_runs_after_row_locks(self):
        dispatch_start = self.flow.index("def mark_shipment_dispatched")
        section = self.flow[dispatch_start:]
        shipment_lock = section.index(
            "CarrierShipment.objects.select_for_update()"
        )
        shipping_lock = section.index(
            "shipping_order_model.objects.select_for_update()"
        )
        order_lock = section.index(
            "order.__class__.objects.select_for_update()"
        )
        validation = section.index("_validate_ready_for_dispatch(")

        self.assertLess(shipment_lock, shipping_lock)
        self.assertLess(shipping_lock, order_lock)
        self.assertLess(order_lock, validation)

    def test_terminal_states_are_distinguished(self):
        self.assertIn('if order_status == "cancelled":', self.flow)
        self.assertIn('if order_status == "delivered":', self.flow)
        self.assertIn(
            'raise ValidationError("El pedido está cancelado.")',
            self.flow,
        )
        self.assertIn(
            'raise ValidationError("El pedido ya fue entregado.")',
            self.flow,
        )

    def test_failure_stays_ready_and_success_goes_transit(self):
        self.assertIn('target_tab = "ready"', self.views)
        self.assertIn('target_tab = "transit"', self.views)
        self.assertIn(
            'f"{dispatch_url}?tab={target_tab}"',
            self.views,
        )

    def test_company_scoped_locks_remain_present(self):
        self.assertIn(
            ".get(pk=shipment_id, company_id=company_id)",
            self.flow,
        )
        self.assertIn(
            ".get(pk=order.pk, company_id=company_id)",
            self.flow,
        )
