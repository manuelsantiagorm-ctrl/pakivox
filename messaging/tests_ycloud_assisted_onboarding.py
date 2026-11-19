from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from companies.models import Company
from inventory.models import Warehouse
from subscriptions.models import Plan, Subscription

from messaging.forms import WhatsAppNumberForm
from messaging.models import WhatsAppNumber


class YCloudAssistedOnboardingTests(TestCase):
    def setUp(self):
        self.plan = Plan.objects.create(
            name="Premium Assisted",
            slug="premium-assisted",
            price="4500.00",
            billing_period="monthly",
            stripe_price_id="price_assisted",
            max_warehouses=2,
            has_whatsapp=True,
            max_whatsapp_numbers=2,
            max_whatsapp_numbers_per_warehouse=2,
            max_whatsapp_agents_per_number=4,
            is_active=True,
        )
        self.company = Company.objects.create(
            name="Tenant Assisted", slug="tenant-assisted", is_active=True
        )
        self.other_company = Company.objects.create(
            name="Other Tenant", slug="other-assisted", is_active=True
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company, name="Bodega propia", code="AS1", is_active=True
        )
        self.other_warehouse = Warehouse.objects.create(
            company=self.other_company, name="Bodega ajena", code="AS2", is_active=True
        )
        today = timezone.localdate()
        Subscription.objects.create(
            company=self.company,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=today,
            end_date=today + timedelta(days=30),
            next_billing_date=today + timedelta(days=30),
            auto_renew=True,
            stripe_customer_id="cus_assisted",
            stripe_subscription_id="sub_assisted",
            stripe_verified_at=timezone.now(),
            stripe_last_paid_event_id="evt_assisted",
            stripe_verified_price_id="price_assisted",
        )

    def form(self, **changes):
        data = {
            "channel_name": "Ventas celular",
            "display_number": "55 3210 3690",
            "warehouse": self.warehouse.pk,
            "connection_mode": WhatsAppNumber.CONNECTION_COEXISTENCE,
            "confirm_number_ownership": "on",
            "confirm_business_app": "on",
        }
        data.update(changes)
        return WhatsAppNumberForm(data=data, company=self.company)

    def test_coexistence_request_is_valid_with_both_confirmations(self):
        form = self.form()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["display_number"], "+525532103690")

    def test_number_ownership_confirmation_is_mandatory(self):
        form = self.form(confirm_number_ownership="")
        self.assertFalse(form.is_valid())
        self.assertIn("confirm_number_ownership", form.errors)

    def test_business_app_confirmation_is_required_for_coexistence(self):
        form = self.form(confirm_business_app="")
        self.assertFalse(form.is_valid())
        self.assertIn("confirm_business_app", form.errors)

    def test_api_only_does_not_require_business_app_confirmation(self):
        form = self.form(
            connection_mode=WhatsAppNumber.CONNECTION_API_ONLY,
            confirm_business_app="",
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_other_tenant_warehouse_is_never_accepted(self):
        form = self.form(warehouse=self.other_warehouse.pk)
        self.assertFalse(form.is_valid())
        self.assertIn("warehouse", form.errors)

    def test_every_channel_gets_a_unique_tracking_reference(self):
        first = WhatsAppNumber.objects.create(
            company=self.company,
            warehouse=self.warehouse,
            channel_name="Uno",
            display_number="+525500000001",
            provider=WhatsAppNumber.PROVIDER_YCLOUD,
        )
        second = WhatsAppNumber.objects.create(
            company=self.company,
            warehouse=self.warehouse,
            channel_name="Dos",
            display_number="+525500000002",
            provider=WhatsAppNumber.PROVIDER_YCLOUD,
        )
        self.assertNotEqual(first.onboarding_reference, second.onboarding_reference)
