from datetime import datetime, timezone as datetime_timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from companies.models import Company
from crm.models import Customer
from dashboard.models import WarehouseFinancialFact
from inventory.models import Warehouse
from sales.models import Order


class AF4WarehouseFinancialFactTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Empresa AF4", slug="empresa-af4")
        self.other_company = Company.objects.create(
            name="Otra empresa AF4",
            slug="otra-empresa-af4",
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="Principal AF4",
        )
        self.other_warehouse = Warehouse.objects.create(
            company=self.other_company,
            name="Bodega ajena AF4",
        )
        self.customer = Customer.objects.create(
            company=self.company,
            full_name="Cliente AF4",
        )
        self.other_customer = Customer.objects.create(
            company=self.other_company,
            full_name="Cliente ajeno AF4",
        )
        self.order = Order.objects.create(
            company=self.company,
            customer=self.customer,
            status="pending_payment",
        )
        self.other_order = Order.objects.create(
            company=self.other_company,
            customer=self.other_customer,
            status="pending_payment",
        )

    def values(self, **overrides):
        effective = datetime(2026, 8, 31, 18, 0, tzinfo=datetime_timezone.utc)
        values = {
            "company": self.company,
            "warehouse": self.warehouse,
            "order": self.order,
            "fact_type": WarehouseFinancialFact.FACT_SALE,
            "idempotency_key": f"af4:{uuid4()}",
            "subtotal_cents": 10000,
            "discount_cents": 1000,
            "tax_cents": 1440,
            "shipping_cents": 500,
            "cost_cents": 5500,
            "total_cents": 10940,
            "gross_profit_cents": 3500,
            "collected_cents": 0,
            "effective_at_utc": effective,
            "effective_local_date": effective.astimezone(
                ZoneInfo("America/Mexico_City")
            ).date(),
            "date_basis": WarehouseFinancialFact.DATE_ORDER_CONFIRMED,
            "occurred_at": effective,
            "timezone_snapshot": "America/Mexico_City",
            "quality_label": WarehouseFinancialFact.QUALITY_REAL,
            "is_estimated": False,
            "allocation_method": WarehouseFinancialFact.ALLOCATION_EXACT_LINE,
            "actor_type": WarehouseFinancialFact.ACTOR_SYSTEM,
            "source_event_type": "af4_test",
            "source_event_id": str(uuid4()),
        }
        values.update(overrides)
        return values

    def create_fact(self, **overrides):
        return WarehouseFinancialFact.objects.create(**self.values(**overrides))

    def test_af1_contract_fields_are_present(self):
        names = {field.name for field in WarehouseFinancialFact._meta.get_fields()}
        required = {
            "rounding_residual_cents",
            "legacy_difference_cents",
            "has_legacy_inconsistency",
            "legacy_inconsistency_reason",
            "effective_at_utc",
            "effective_local_date",
            "date_basis",
            "occurred_at",
            "recorded_at",
            "timezone_snapshot",
            "quality_label",
            "is_estimated",
            "allocation_method",
            "actor",
            "actor_type",
            "actor_snapshot",
            "source_event_type",
            "source_event_id",
            "metadata",
        }
        self.assertTrue(required.issubset(names))

    def test_valid_assigned_fact_is_persisted_in_integer_cents(self):
        fact = self.create_fact()
        self.assertEqual(fact.total_cents, 10940)
        self.assertEqual(fact.gross_profit_cents, 3500)
        self.assertEqual(fact.currency, "MXN")

    def test_unassigned_is_bucket_not_fake_warehouse(self):
        fact = self.create_fact(
            warehouse=None,
            allocation_method=WarehouseFinancialFact.ALLOCATION_UNASSIGNED,
        )
        self.assertIsNone(fact.warehouse_id)
        with self.assertRaises(ValidationError):
            self.create_fact(warehouse=None)

    def test_cross_company_warehouse_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.create_fact(warehouse=self.other_warehouse)

    def test_cross_company_order_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.create_fact(order=self.other_order)

    def test_local_date_must_match_timezone_snapshot(self):
        with self.assertRaises(ValidationError):
            self.create_fact(effective_local_date=datetime(2026, 9, 1).date())

    def test_invalid_iana_timezone_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.create_fact(timezone_snapshot="Mexico/Inventada")

    def test_estimated_flag_and_quality_must_match(self):
        with self.assertRaises(ValidationError):
            self.create_fact(
                quality_label=WarehouseFinancialFact.QUALITY_ESTIMATED,
                is_estimated=False,
            )
        fact = self.create_fact(
            quality_label=WarehouseFinancialFact.QUALITY_ESTIMATED,
            is_estimated=True,
        )
        self.assertTrue(fact.is_estimated)

    def test_row_formula_must_reconcile(self):
        with self.assertRaises(ValidationError):
            self.create_fact(total_cents=10941)
        with self.assertRaises(ValidationError):
            self.create_fact(gross_profit_cents=3501)

    def test_legacy_difference_requires_flag_and_reason(self):
        with self.assertRaises(ValidationError):
            self.create_fact(
                legacy_difference_cents=3,
                total_cents=10943,
                has_legacy_inconsistency=True,
            )
        fact = self.create_fact(
            legacy_difference_cents=3,
            total_cents=10943,
            has_legacy_inconsistency=True,
            legacy_inconsistency_reason="Pedido histórico no conciliado",
        )
        self.assertTrue(fact.has_legacy_inconsistency)

    def test_idempotency_key_is_unique_inside_company(self):
        key = "af4:duplicate-event"
        self.create_fact(idempotency_key=key)
        with self.assertRaises(ValidationError):
            self.create_fact(idempotency_key=key)

    def test_model_instance_is_immutable_and_not_deletable(self):
        fact = self.create_fact()
        fact.total_cents = 0
        with self.assertRaises(ValidationError):
            fact.save()
        with self.assertRaises(ValidationError):
            fact.delete()

    def test_queryset_update_delete_and_bulk_update_are_blocked(self):
        fact = self.create_fact()
        queryset = WarehouseFinancialFact.objects.filter(pk=fact.pk)
        with self.assertRaises(ValidationError):
            queryset.update(total_cents=0)
        with self.assertRaises(ValidationError):
            queryset.delete()
        with self.assertRaises(ValidationError):
            WarehouseFinancialFact.objects.bulk_update([fact], ["total_cents"])

    def test_financial_relations_are_protected(self):
        self.create_fact()
        with self.assertRaises(ProtectedError):
            self.warehouse.delete()

    def test_company_scope_manager_does_not_leak_other_company(self):
        self.create_fact()
        WarehouseFinancialFact.objects.create(
            **self.values(
                company=self.other_company,
                warehouse=self.other_warehouse,
                order=self.other_order,
            )
        )
        visible = WarehouseFinancialFact.objects.for_company(self.company)
        self.assertEqual(visible.count(), 1)
        self.assertTrue(all(item.company_id == self.company.id for item in visible))

