from datetime import datetime, timezone as datetime_timezone

from django.core.exceptions import ValidationError
from django.test import TestCase

from companies.models import Company
from companies.timezones import company_local_date, company_month_bounds_utc
from crm.models import Customer
from sales.models import Order


class AF2CompanyTimezoneTests(TestCase):
    def test_default_timezone_is_mexico_city(self):
        company = Company.objects.create(name="Empresa AF2", slug="empresa-af2")
        self.assertEqual(company.timezone, "America/Mexico_City")

    def test_invalid_iana_timezone_is_rejected_on_save(self):
        with self.assertRaises(ValidationError):
            Company.objects.create(
                name="Zona inválida",
                slug="zona-invalida",
                timezone="Mexico/Inventada",
            )

    def test_month_boundary_uses_company_timezone_not_server_timezone(self):
        company = Company.objects.create(name="Empresa límite", slug="empresa-limite")
        instant = datetime(2026, 9, 1, 5, 30, tzinfo=datetime_timezone.utc)
        self.assertEqual(company_local_date(company, instant).isoformat(), "2026-08-31")

        start, end = company_month_bounds_utc(company, 2026, 8)
        self.assertEqual(start.isoformat(), "2026-08-01T06:00:00+00:00")
        self.assertEqual(end.isoformat(), "2026-09-01T06:00:00+00:00")


class AF2OrderConfirmationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Ventas AF2", slug="ventas-af2")
        self.customer = Customer.objects.create(
            company=self.company,
            full_name="Cliente AF2",
        )

    def order(self, **values):
        return Order.objects.create(
            company=self.company,
            customer=self.customer,
            **values,
        )

    def test_draft_is_not_financially_confirmed(self):
        order = self.order(status="draft")
        self.assertIsNone(order.confirmed_at)
        self.assertFalse(order.confirmation_date_is_inferred)

    def test_leaving_draft_sets_real_confirmation_time(self):
        order = self.order(status="draft")
        order.status = "pending_payment"
        order.save(update_fields=["status"])
        order.refresh_from_db()
        self.assertIsNotNone(order.confirmed_at)
        self.assertFalse(order.confirmation_date_is_inferred)

    def test_direct_non_draft_creation_is_confirmed(self):
        order = self.order(status="paid")
        self.assertIsNotNone(order.confirmed_at)
        self.assertFalse(order.confirmation_date_is_inferred)

    def test_confirmation_time_cannot_be_rewritten(self):
        order = self.order(status="pending_payment")
        original = order.confirmed_at
        order.confirmed_at = datetime(2035, 1, 1, tzinfo=datetime_timezone.utc)
        order.status = "paid"
        order.save(update_fields=["confirmed_at", "status"])
        order.refresh_from_db()
        self.assertEqual(order.confirmed_at, original)

