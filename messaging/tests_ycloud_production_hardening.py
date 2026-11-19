from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from companies.models import Company
from inventory.models import Warehouse
from subscriptions.models import Plan, Subscription

from messaging.forms import WhatsAppNumberForm
from messaging.models import WhatsAppNumber
from messaging.onboarding_admin import transition_onboarding
from messaging.onboarding_models import OnboardingStatusEvent


class YCloudProductionHardeningTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_superuser(
            username="owner9", email="owner9@example.com", password="test-pass"
        )
        self.company = Company.objects.create(
            name="Empresa Fase 9", slug="empresa-fase-9", is_active=True
        )
        self.other_company = Company.objects.create(
            name="Empresa Ajena Fase 9", slug="empresa-ajena-fase-9", is_active=True
        )
        self.plan = Plan.objects.create(
            name="Premium Fase 9", slug="premium-fase-9", price="4500.00",
            billing_period="monthly", stripe_price_id="price_f9",
            max_warehouses=10, has_whatsapp=True, max_whatsapp_numbers=4,
            max_whatsapp_numbers_per_warehouse=2,
            max_whatsapp_agents_per_number=4, is_active=True,
        )
        today = timezone.localdate()
        Subscription.objects.create(
            company=self.company, plan=self.plan,
            status=Subscription.STATUS_ACTIVE, start_date=today,
            end_date=today + timedelta(days=30),
            next_billing_date=today + timedelta(days=30), auto_renew=True,
            stripe_customer_id="cus_f9", stripe_subscription_id="sub_f9",
            stripe_verified_at=timezone.now(),
            stripe_last_paid_event_id="evt_f9",
            stripe_verified_price_id="price_f9",
        )
        self.warehouse_a = Warehouse.objects.create(
            company=self.company, name="Norte", code="F9A", is_active=True
        )
        self.warehouse_b = Warehouse.objects.create(
            company=self.company, name="Sur", code="F9B", is_active=True
        )
        self.other_warehouse = Warehouse.objects.create(
            company=self.other_company, name="Ajena", code="F9X", is_active=True
        )

    def form(self, number, warehouse=None, **changes):
        data = {
            "channel_name": "Ventas Fase 9",
            "display_number": number,
            "warehouse": (warehouse or self.warehouse_a).pk,
            "connection_mode": WhatsAppNumber.CONNECTION_COEXISTENCE,
            "confirm_number_ownership": "on",
            "confirm_business_app": "on",
        }
        data.update(changes)
        return WhatsAppNumberForm(data=data, company=self.company)

    def create_channel(self, suffix="01", warehouse=None):
        return WhatsAppNumber.objects.create(
            company=self.company, warehouse=warehouse or self.warehouse_a,
            provider=WhatsAppNumber.PROVIDER_YCLOUD,
            channel_name=f"Canal {suffix}", display_number=f"+525500009{suffix}",
            activation_status=WhatsAppNumber.STATUS_PENDING_ACTIVATION,
        )

    def test_mexican_national_number_becomes_e164(self):
        form = self.form("55 3210-3690")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["display_number"], "+525532103690")

    def test_mexican_legacy_mobile_prefix_is_removed(self):
        form = self.form("+52 1 55 3210 3690")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["display_number"], "+525532103690")

    def test_explicit_international_number_keeps_country_code(self):
        form = self.form("+14155552671")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["display_number"], "+14155552671")

    def test_other_tenant_warehouse_is_rejected(self):
        form = self.form("5532103690", warehouse=self.other_warehouse)
        self.assertFalse(form.is_valid())
        self.assertIn("warehouse", form.errors)

    def test_two_warehouses_keep_independent_channel_ownership(self):
        first = self.create_channel("11", self.warehouse_a)
        second = self.create_channel("12", self.warehouse_b)
        self.assertEqual(first.company_id, second.company_id)
        self.assertNotEqual(first.warehouse_id, second.warehouse_id)

    def test_tracking_references_are_unique_with_existing_rows(self):
        references = {
            str(self.create_channel("01", self.warehouse_a).onboarding_reference),
            str(self.create_channel("02", self.warehouse_a).onboarding_reference),
            str(self.create_channel("03", self.warehouse_b).onboarding_reference),
            str(self.create_channel("04", self.warehouse_b).onboarding_reference),
        }
        self.assertEqual(len(references), 4)

    def test_database_rejects_duplicate_tracking_reference(self):
        first = self.create_channel("21")
        second = self.create_channel("22")
        second.onboarding_reference = first.onboarding_reference
        with self.assertRaises((IntegrityError, ValidationError)):
            second.save()

    def test_active_channel_requires_real_ycloud_confirmation(self):
        channel = self.create_channel("31")
        transition_onboarding(
            channel_id=channel.pk, new_status=WhatsAppNumber.STATUS_VERIFYING,
            actor=self.owner,
        )
        with self.assertRaises(ValidationError):
            transition_onboarding(
                channel_id=channel.pk, new_status=WhatsAppNumber.STATUS_ACTIVE,
                actor=self.owner,
            )

    def test_complete_transition_history_is_contiguous(self):
        channel = self.create_channel("41")
        transition_onboarding(channel_id=channel.pk, new_status=WhatsAppNumber.STATUS_VERIFYING, actor=self.owner)
        channel.refresh_from_db()
        channel.phone_number_id = "pn-f9"
        channel.ycloud_waba_id = "waba-f9"
        channel.ycloud_status = "CONNECTED"
        channel.save()
        transition_onboarding(channel_id=channel.pk, new_status=WhatsAppNumber.STATUS_ACTIVE, actor=self.owner)
        transition_onboarding(channel_id=channel.pk, new_status=WhatsAppNumber.STATUS_SUSPENDED, actor=self.owner, internal_note="Prueba controlada")
        events = list(channel.onboarding_events.order_by("created_at", "pk"))
        self.assertEqual(len(events), 3)
        self.assertTrue(all(a.new_status == b.previous_status for a, b in zip(events, events[1:])))

    def test_failed_audit_write_rolls_back_channel_transition(self):
        channel = self.create_channel("51")
        with patch.object(OnboardingStatusEvent.objects, "create", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                transition_onboarding(
                    channel_id=channel.pk,
                    new_status=WhatsAppNumber.STATUS_VERIFYING,
                    actor=self.owner,
                )
        channel.refresh_from_db()
        self.assertEqual(channel.activation_status, WhatsAppNumber.STATUS_PENDING_ACTIVATION)

    def test_real_database_audit_reports_clean_state(self):
        channel = self.create_channel("61")
        transition_onboarding(channel_id=channel.pk, new_status=WhatsAppNumber.STATUS_VERIFYING, actor=self.owner)
        output = StringIO()
        call_command("audit_ycloud_onboarding", "--strict", stdout=output)
        self.assertIn("AUDITORIA YCLOUD OK", output.getvalue())

    def test_audit_detects_broken_event_sequence(self):
        channel = self.create_channel("71")
        OnboardingStatusEvent.objects.create(
            whatsapp_number=channel, company=self.company,
            warehouse=self.warehouse_a, previous_status="unexpected",
            new_status=WhatsAppNumber.STATUS_VERIFYING, created_by=self.owner,
        )
        OnboardingStatusEvent.objects.create(
            whatsapp_number=channel, company=self.company,
            warehouse=self.warehouse_a,
            previous_status=WhatsAppNumber.STATUS_ACTIVE,
            new_status=WhatsAppNumber.STATUS_SUSPENDED, created_by=self.owner,
        )
        with self.assertRaises(CommandError):
            call_command("audit_ycloud_onboarding", "--strict", stdout=StringIO())
