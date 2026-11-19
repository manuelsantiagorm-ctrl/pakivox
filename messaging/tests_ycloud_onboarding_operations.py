from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from datetime import timedelta

from django.utils import timezone

from companies.models import Company
from inventory.models import Warehouse
from subscriptions.models import Plan, Subscription
from .models import WhatsAppNumber
from .onboarding_admin import transition_onboarding
from .onboarding_models import OnboardingStatusEvent


class YCloudOnboardingOperationsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_superuser(username="owner8", email="owner8@example.com", password="test-pass")
        self.regular = User.objects.create_user(username="regular8", password="test-pass")
        self.company = Company.objects.create(name="Empresa Fase 8")
        self.other_company = Company.objects.create(name="Empresa Ajena Fase 8")
        self.plan = Plan.objects.create(name="Premium Fase 8", slug="premium-fase-8", price="4500.00", billing_period="monthly", stripe_price_id="price_f8", max_warehouses=2, has_whatsapp=True, max_whatsapp_numbers=2, max_whatsapp_numbers_per_warehouse=2, max_whatsapp_agents_per_number=4, is_active=True)
        today = timezone.localdate()
        Subscription.objects.create(company=self.company, plan=self.plan, status=Subscription.STATUS_ACTIVE, start_date=today, end_date=today + timedelta(days=30), next_billing_date=today + timedelta(days=30), auto_renew=True, stripe_customer_id="cus_f8", stripe_subscription_id="sub_f8", stripe_verified_at=timezone.now(), stripe_last_paid_event_id="evt_f8", stripe_verified_price_id="price_f8")
        self.warehouse = Warehouse.objects.create(company=self.company, name="Principal", code="F8A", is_active=True)
        self.other_warehouse = Warehouse.objects.create(company=self.other_company, name="Ajena", code="F8B", is_active=True)
        self.channel = WhatsAppNumber.objects.create(company=self.company, warehouse=self.warehouse, provider=WhatsAppNumber.PROVIDER_YCLOUD, channel_name="Ventas", display_number="+525500000081", activation_status=WhatsAppNumber.STATUS_PENDING_ACTIVATION)

    def test_regular_user_cannot_open_owner_panel(self):
        self.client.force_login(self.regular)
        response = self.client.get(reverse("messaging:owner_onboarding_list"))
        self.assertEqual(response.status_code, 302)

    def test_owner_can_filter_panel(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("messaging:owner_onboarding_list"), {"status": WhatsAppNumber.STATUS_PENDING_ACTIVATION})
        self.assertContains(response, "Empresa Fase 8")

    def test_pending_cannot_jump_directly_to_active(self):
        with self.assertRaises(ValidationError):
            transition_onboarding(channel_id=self.channel.pk, new_status=WhatsAppNumber.STATUS_ACTIVE, actor=self.owner)

    def test_transition_creates_tenant_bound_audit_event(self):
        transition_onboarding(channel_id=self.channel.pk, new_status=WhatsAppNumber.STATUS_VERIFYING, actor=self.owner, public_note="Estamos verificando.", internal_note="Documentación recibida.")
        event = OnboardingStatusEvent.objects.get()
        self.assertEqual(event.company_id, self.company.pk)
        self.assertEqual(event.warehouse_id, self.warehouse.pk)
        self.assertEqual(event.whatsapp_number_id, self.channel.pk)

    def test_activation_requires_verifying_first_and_enables_channel(self):
        transition_onboarding(channel_id=self.channel.pk, new_status=WhatsAppNumber.STATUS_VERIFYING, actor=self.owner)
        self.channel.refresh_from_db()
        self.channel.phone_number_id = "pn-f8"
        self.channel.ycloud_waba_id = "waba-f8"
        self.channel.ycloud_status = "CONNECTED"
        self.channel.save()
        transition_onboarding(channel_id=self.channel.pk, new_status=WhatsAppNumber.STATUS_ACTIVE, actor=self.owner)
        self.channel.refresh_from_db()
        self.assertTrue(self.channel.is_active)
        self.assertTrue(self.channel.ycloud_enabled)
        self.assertIsNotNone(self.channel.activated_at)

    def test_disconnection_disables_transport(self):
        # Representa un canal realmente verificado antes de activarlo. El
        # modelo debe conservar su candado y rechazar activaciones YCloud
        # incompletas también durante las pruebas.
        self.channel.phone_number_id = "pn-disconnect-f8"
        self.channel.ycloud_waba_id = "waba-disconnect-f8"
        self.channel.ycloud_status = "CONNECTED"
        self.channel.activation_status = WhatsAppNumber.STATUS_ACTIVE
        self.channel.is_active = True
        self.channel.ycloud_enabled = True
        self.channel.save()
        transition_onboarding(channel_id=self.channel.pk, new_status=WhatsAppNumber.STATUS_DISCONNECTED, actor=self.owner, internal_note="Solicitado por el cliente")
        self.channel.refresh_from_db()
        self.assertFalse(self.channel.is_active)
        self.assertFalse(self.channel.ycloud_enabled)

    def test_event_rejects_mismatched_company_and_warehouse(self):
        event = OnboardingStatusEvent(whatsapp_number=self.channel, company=self.other_company, warehouse=self.other_warehouse, previous_status="a", new_status="b", created_by=self.owner)
        with self.assertRaises(ValidationError):
            event.save()

    def test_transition_endpoint_rejects_get(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("messaging:owner_onboarding_transition", args=[self.channel.pk]))
        self.assertEqual(response.status_code, 405)
