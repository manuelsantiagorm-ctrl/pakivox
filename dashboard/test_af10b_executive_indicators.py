from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
import json
from pathlib import Path

from django.test import TestCase
from django.template.loader import get_template

from companies.models import Company
from crm.models import Customer
from dashboard.services.ceo_executive_indicators import (
    build_ceo_executive_indicator_context,
)
from dashboard.services.ceo_financial_dashboard import build_ceo_financial_context
from dashboard.services.warehouse_financial_attribution import attribute_order_sale
from inventory.models import Product, ProductVariant, Warehouse
from sales.models import Order, OrderItem
from warehouse.models import OrderFulfillment, OrderItemAllocation


class AF10BExecutiveIndicatorsTests(TestCase):
    def setUp(self):
        self.instant = datetime(2026, 8, 13, 18, 0, tzinfo=dt_timezone.utc)
        self.company = Company.objects.create(
            name="Empresa AF10B",
            slug="empresa-af10b",
            timezone="America/Mexico_City",
        )
        self.foreign_company = Company.objects.create(
            name="Empresa ajena AF10B",
            slug="empresa-ajena-af10b",
            timezone="America/Mexico_City",
        )
        self.customer = Customer.objects.create(
            company=self.company,
            full_name="Cliente AF10B",
        )
        foreign_customer = Customer.objects.create(
            company=self.foreign_company,
            full_name="Cliente ajeno AF10B",
        )
        self.warehouse = Warehouse.objects.create(company=self.company, name="Centro")
        product = Product.objects.create(company=self.company, name="Producto AF10B")
        variant = ProductVariant.objects.create(
            company=self.company,
            product=product,
            sku="AF10B-SKU",
            sale_price=Decimal("100.00"),
            cost_price=Decimal("40.00"),
        )
        self.delivered = Order.objects.create(
            company=self.company,
            customer=self.customer,
            folio="AF10B-DELIVERED",
            status="delivered",
            confirmed_at=self.instant - timedelta(days=3),
        )
        self.in_process = Order.objects.create(
            company=self.company,
            customer=self.customer,
            folio="AF10B-PACKED",
            status="packed",
            confirmed_at=self.instant - timedelta(days=2),
        )
        Order.objects.create(
            company=self.company,
            customer=self.customer,
            folio="AF10B-OLD",
            status="delivered",
            confirmed_at=self.instant - timedelta(days=45),
        )
        Order.objects.create(
            company=self.foreign_company,
            customer=foreign_customer,
            folio="AF10B-FOREIGN",
            status="delivered",
            confirmed_at=self.instant - timedelta(days=1),
        )
        item = OrderItem.objects.create(
            order=self.delivered,
            variant=variant,
            quantity=1,
            unit_price=Decimal("100.00"),
            unit_cost=Decimal("40.00"),
        )
        fulfillment = OrderFulfillment.objects.create(
            company=self.company,
            order=self.delivered,
            warehouse=self.warehouse,
            sequence=1,
            status=OrderFulfillment.STATUS_DELIVERED,
        )
        OrderItemAllocation.objects.create(
            company=self.company,
            fulfillment=fulfillment,
            order_item=item,
            variant=variant,
            commercial_quantity=Decimal("1"),
            internal_quantity=1,
        )
        self.delivered.refresh_from_db()
        attribute_order_sale(
            company_id=self.company.id,
            order_id=self.delivered.id,
            source_event_type="af10b_seed",
            source_event_id=f"order:{self.delivered.id}",
            occurred_at=self.delivered.confirmed_at,
        )

    def indicator_context(self):
        return build_ceo_executive_indicator_context(
            company=self.company,
            scoped_orders=Order.objects.all(),
            supplier_balance_due_total=Decimal("18010.00"),
            customer_balance_due_total=Decimal("250.00"),
            pending_pickings=2,
            pending_packings=1,
            pending_shippings=12,
            now=self.instant,
        )

    def test_cohort_is_rolling_and_does_not_leak_other_company(self):
        result = self.indicator_context()
        self.assertEqual(result["executive_cohort_total"], 2)
        self.assertEqual(result["executive_cohort_delivered"], 1)
        self.assertEqual(result["executive_fulfillment_rate"], Decimal("50.0"))

    def test_load_and_capital_are_not_mislabeled_as_bank_liquidity(self):
        result = self.indicator_context()
        self.assertEqual(result["executive_stalled_total"], 15)
        self.assertEqual(result["executive_stalled_counts"], [2, 1, 12])
        self.assertEqual(result["executive_capital_committed"], Decimal("18010.00"))
        self.assertEqual(result["executive_operating_position"], Decimal("-17760.00"))
        self.assertNotIn("liquidity", " ".join(result.keys()).lower())

    def test_financial_book_exposes_exactly_thirty_rolling_points(self):
        result = build_ceo_financial_context(
            company=self.company,
            active_warehouse=self.warehouse,
            accessible_warehouse_ids=[self.warehouse.id],
            now=self.instant,
        )
        labels = json.loads(result["executive_collection_labels"])
        sales = json.loads(result["executive_collection_sales"])
        collected = json.loads(result["executive_collection_collected"])
        self.assertEqual(len(labels), 30)
        self.assertEqual(len(sales), 30)
        self.assertEqual(len(collected), 30)
        self.assertEqual(sum(sales), 100.0)

    def test_summary_template_compiles_and_has_three_chart_targets(self):
        self.assertIsNotNone(get_template("dashboard/ceo.html"))
        source = Path(
            "templates/dashboard/partials/executive_home.html"
        ).read_text(encoding="utf-8")
        self.assertEqual(source.count('id="executiveCollectionChartAF10B"'), 1)
        self.assertEqual(source.count('id="executiveFulfillmentChartAF10B"'), 1)
        self.assertEqual(source.count('id="executiveCapitalChartAF10B"'), 1)
        self.assertIn('id="pakivox-executive-af10b-script"', source)

    def test_chart_contract_has_tooltips_motion_and_accessibility(self):
        source = "\n".join(
            Path(path).read_text(encoding="utf-8")
            for path in (
                "templates/dashboard/partials/executive_home.html",
                "static/js/tablero_resumen.js",
                "static/style/tablero_resumen.css",
            )
        )
        self.assertIn('interaction:{mode:"index",intersect:false}', source)
        self.assertIn('duration:reduced?0:1100', source)
        self.assertIn('easing:"easeOutQuart"', source)
        self.assertIn("prefers-reduced-motion", source)
        self.assertIn('aria-label="Indicadores cruzados del negocio"', source)
        self.assertIn("No representa saldo bancario", source)
