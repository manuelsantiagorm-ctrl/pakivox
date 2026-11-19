from datetime import datetime, timezone as dt_timezone
from dashboard.test_ceo_ui_production import read_ceo_source
from decimal import Decimal
from unittest.mock import patch
import json
import re

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.template.loader import get_template

from companies.models import Company
from crm.models import Customer
from dashboard.services.ceo_financial_dashboard import build_ceo_financial_context
from dashboard.services.warehouse_financial_attribution import attribute_order_sale
from dashboard.services.warehouse_financial_reporting import get_financial_summary
from inventory.models import Product, ProductVariant, Warehouse
from payments.models import Payment
from sales.models import Order, OrderItem
from warehouse.models import OrderFulfillment, OrderItemAllocation


class AF8FinanceExecutiveDesignTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Empresa AF7B", slug="empresa-af7b",
            timezone="America/Mexico_City",
        )
        self.other = Company.objects.create(
            name="Otra AF7B", slug="otra-af7b",
            timezone="America/Mexico_City",
        )
        customer = Customer.objects.create(
            company=self.company, full_name="Cliente AF7B"
        )
        product = Product.objects.create(company=self.company, name="Producto AF7B")
        variant = ProductVariant.objects.create(
            company=self.company, product=product, sku="AF7B-SKU",
            sale_price=Decimal("100.00"), cost_price=Decimal("40.00"),
        )
        self.south = Warehouse.objects.create(company=self.company, name="Sur")
        self.north = Warehouse.objects.create(company=self.company, name="Norte")
        self.instant = datetime(2026, 8, 13, 18, 0, tzinfo=dt_timezone.utc)
        self.order = Order.objects.create(
            company=self.company, customer=customer,
            status="pending_payment", confirmed_at=self.instant,
        )
        item = OrderItem.objects.create(
            order=self.order, variant=variant, quantity=2,
            unit_price=Decimal("100.00"), unit_cost=Decimal("40.00"),
        )
        for sequence, warehouse in enumerate((self.south, self.north), start=1):
            fulfillment = OrderFulfillment.objects.create(
                company=self.company, order=self.order, warehouse=warehouse,
                sequence=sequence, status=OrderFulfillment.STATUS_PENDING,
            )
            OrderItemAllocation.objects.create(
                company=self.company, fulfillment=fulfillment, order_item=item,
                variant=variant, commercial_quantity=Decimal("1"),
                internal_quantity=1,
            )
        self.order.refresh_from_db()
        attribute_order_sale(
            company_id=self.company.id, order_id=self.order.id,
            source_event_type="af7b_seed", source_event_id=f"order:{self.order.id}",
            occurred_at=self.instant,
        )
        payment = Payment.objects.create(
            company=self.company, order=self.order, folio="AF7B-PAY",
            payment_method="transfer", payment_type="deposit",
            status="under_review", amount=Decimal("80.00"),
        )
        with patch(
            "payments.models.timezone.now",
            return_value=self.instant,
        ):
            payment.approve()

    def context(self, *, active=None, accessible=None):
        return build_ceo_financial_context(
            company=self.company,
            active_warehouse=active,
            accessible_warehouse_ids=(
                accessible if accessible is not None
                else [self.south.id, self.north.id]
            ),
            now=self.instant,
        )

    def test_all_warehouses_uses_book_and_preserves_split(self):
        result = self.context()
        self.assertEqual(result["sales_total"], Decimal("200.00"))
        self.assertEqual(result["paid_total"], Decimal("80.00"))
        self.assertEqual(result["gross_profit_total"], Decimal("120.00"))
        self.assertEqual(result["finance_scope_label"], "Todas las bodegas")
        self.assertEqual(result["finance_sales_quality"], "Atribuido")
        self.assertEqual(result["finance_collected_quality"], "Estimado")

    def test_selected_warehouse_changes_dashboard_money(self):
        south = self.context(active=self.south)
        north = self.context(active=self.north)
        self.assertEqual(south["sales_total"], Decimal("100.00"))
        self.assertEqual(north["sales_total"], Decimal("100.00"))
        self.assertEqual(south["paid_total"], Decimal("40.00"))
        self.assertTrue(south["finance_is_warehouse_scope"])
        self.assertFalse(south["finance_supports_sku_rankings"])

    def test_restricted_all_scope_does_not_leak_other_warehouse(self):
        result = self.context(accessible=[self.south.id])
        self.assertEqual(result["sales_total"], Decimal("100.00"))
        self.assertEqual(result["paid_total"], Decimal("40.00"))
        self.assertEqual(result["finance_scope_label"], "Bodegas autorizadas")
        self.assertFalse(result["finance_supports_sku_rankings"])

    def test_cross_company_warehouse_list_is_rejected(self):
        foreign = Warehouse.objects.create(company=self.other, name="Ajena")
        with self.assertRaises(ValidationError):
            get_financial_summary(
                company_id=self.company.id,
                start_date=self.instant.date(), end_date=self.instant.date(),
                warehouse_ids=[self.south.id, foreign.id],
                include_unassigned=False,
            )

    def test_context_exposes_chart_from_same_book_totals(self):
        result = self.context(active=self.south)
        self.assertEqual(result["profitability_counts"], [100.0, 40.0, 60.0])
        self.assertEqual(sum(json.loads(result["finance_trend_sales"])), 100.0)
        self.assertEqual(sum(json.loads(result["finance_trend_collected"])), 40.0)
        self.assertEqual(sum(json.loads(result["finance_trend_profit"])), 60.0)
        self.assertTrue(result["finance_trend_has_data"])

    def test_ceo_template_compiles_with_financial_ledger_blocks(self):
        template = get_template("dashboard/ceo.html")
        self.assertIsNotNone(template)

    def test_finance_design_has_one_visible_executive_chart_target(self):
        from pathlib import Path

        source = read_ceo_source()
        self.assertEqual(source.count('id="financeExecutiveV8"'), 1)
        self.assertEqual(source.count('id="profitabilityTrendChartV8"'), 1)
        self.assertEqual(source.count('id="profitabilityMixChartV8"'), 1)
        self.assertIn('id="financeV8Details" hidden', source)
        self.assertIn('pakivox-finance-executive-v8-script', source)
        self.assertIn('prefers-reduced-motion:reduce', source)
        self.assertIn('interaction:{mode:"index",intersect:false}', source)
        self.assertIn('new Intl.NumberFormat("es-MX"', source)
        self.assertIn('animation:{duration:1100,easing:"easeOutQuart"}', source)
        self.assertIn('Cobrado acumulado', source)
        self.assertIn('Alertas prioritarias', source)
        self.assertIn('La liquidez bancaria se habilitará', source)
        self.assertNotIn('Liquidez disponible', source)

    def test_legacy_finance_is_not_visible_with_v8(self):
        from pathlib import Path

        source = read_ceo_source()
        # El CSS ya no esta minificado: se compara sin espacios.
        compact = re.sub(r"\s+", "", source).replace(";}", "}")
        self.assertIn(
            '#ceoFinancialOverview~[data-ceo-group="finance"]{display:none!important}',
            compact,
        )
        self.assertIn(
            'document.getElementById("profitabilityTrendChartV8")',
            source,
        )
