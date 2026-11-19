from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import inspect

from django.conf import settings
from django.test import SimpleTestCase

from sales import views
from sales.simple_ui import build_order_simple_step


class PS6ASimpleOrderUITests(SimpleTestCase):
    def _order(self, *, status="draft", balance="0.00", total="100.00"):
        return SimpleNamespace(
            status=status,
            balance_due=Decimal(balance),
            total=Decimal(total),
        )
    def _template(self):
        base_dir = Path(settings.BASE_DIR) / "templates" / "sales"
        main = (base_dir / "detalle_pedido.html").read_text(encoding="utf-8")

        partials_dir = base_dir / "partials"
        partial_names = [
            "_order_kpis.html",
            "_next_step.html",
            "_productos_pedido_items.html",
            "_finance_dynamic.html",
            "_order_validations.html",
        ]
        partials_text = "\n".join(
            (partials_dir / name).read_text(encoding="utf-8")
            for name in partial_names
        )

        return main + "\n" + partials_text

    def test_empty_editable_order_has_one_add_products_action(self):
        step = build_order_simple_step(
            self._order(total="0.00"),
            has_items=False,
            can_edit_products=True,
            can_view_payments=False,
        )
        self.assertEqual(step.code, "add_products")
        self.assertEqual(step.action_label, "Agregar productos")
        self.assertEqual(step.action_url, "#productos-pedido")

    def test_balance_due_routes_to_collections_only_when_visible(self):
        step = build_order_simple_step(
            self._order(status="partial_paid", balance="250.00"),
            has_items=True,
            can_edit_products=True,
            can_view_payments=True,
            payments_url="/payments/pedido/10/",
        )
        self.assertEqual(step.code, "collection")
        self.assertEqual(step.action_label, "Gestionar cobro")
        self.assertEqual(step.action_url, "/payments/pedido/10/")
        self.assertIn("$250.00", step.description)

    def test_sales_only_user_does_not_get_payment_link(self):
        step = build_order_simple_step(
            self._order(status="pending_payment", balance="250.00"),
            has_items=True,
            can_edit_products=False,
            can_view_payments=False,
            payments_url="/payments/pedido/10/",
        )
        self.assertEqual(step.code, "collection_wait")
        self.assertEqual(step.action_url, "")
        self.assertIn("equipo de Cobros", step.description)

    def test_paid_order_does_not_offer_direct_picking_from_sales(self):
        step = build_order_simple_step(
            self._order(status="paid", balance="0.00"),
            has_items=True,
            can_edit_products=True,
            can_view_payments=True,
            payments_url="/payments/pedido/10/",
        )
        self.assertEqual(step.code, "ready_for_warehouse")
        self.assertEqual(step.action_url, "")
        self.assertIn("No necesitas generar picking", step.description)

    def test_operational_and_closed_states_are_human_readable(self):
        expected = {
            "picking": "warehouse",
            "packed": "warehouse",
            "shipped": "shipped",
            "delivered": "delivered",
            "cancelled": "cancelled",
        }
        for status, code in expected.items():
            with self.subTest(status=status):
                step = build_order_simple_step(
                    self._order(status=status, balance="0.00"),
                    has_items=True,
                    can_edit_products=False,
                    can_view_payments=False,
                )
                self.assertEqual(step.code, code)
                self.assertEqual(step.action_url, "")

    def test_template_has_single_primary_workflow_and_no_direct_picking(self):
        template = self._template()
        self.assertIn('data-ps6a-primary="order"', template)
        self.assertNotIn("warehouse:generar_picking", template)
        self.assertIn("Más opciones", template)

    def test_cost_and_margin_ui_are_guarded_by_finance_capability(self):
        template = self._template()
        self.assertIn(
            '{% if can_view_costs %}\n  <section class="profitability-section">',
            template,
        )
        self.assertIn(
            '{% if can_view_costs %}\n          <div class="item-profit-grid">',
            template,
        )
        source = inspect.getsource(views._build_order_detail_context)
        self.assertIn('has_capability("finance.costs.view")', source)

    def test_editing_and_payment_links_are_capability_gated(self):
        template = self._template()
        self.assertIn("{% if can_operate_sales %}", template)
        self.assertIn("{% if can_view_payments %}", template)
        source = inspect.getsource(views._build_order_detail_context)
        self.assertIn('has_capability("sales.create")', source)
        self.assertIn('has_capability("payments.view")', source)
