from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from companies.models import Company
from inventory.models import Warehouse
from subscriptions.models import Plan, Subscription
from messaging.models import WhatsAppNumber
from messaging.ycloud_api import (
    YCloudWhatsAppConfigurationError, retrieve_ycloud_phone_number,
)
from messaging.ycloud_onboarding import verify_and_activate_ycloud_channel


@override_settings(YCLOUD_ENABLED=True, YCLOUD_API_KEY="secret", YCLOUD_API_BASE_URL="https://api.ycloud.com/v2")
class YCloudOnboardingTests(TestCase):
    def setUp(self):
        self.plan = Plan.objects.create(
            name="Premium YCloud 5", slug="premium-ycloud-5", price="4500.00",
            billing_period="monthly", stripe_price_id="price_yc5", max_warehouses=2,
            has_whatsapp=True, max_whatsapp_numbers=2,
            max_whatsapp_numbers_per_warehouse=2, max_whatsapp_agents_per_number=4,
            is_active=True,
        )
        self.company = Company.objects.create(name="Tenant YCloud 5", slug="tenant-yc5", is_active=True)
        self.warehouse = Warehouse.objects.create(company=self.company, name="Bodega YC5", code="YC5", is_active=True)
        today = timezone.localdate()
        Subscription.objects.create(
            company=self.company, plan=self.plan, status=Subscription.STATUS_ACTIVE,
            start_date=today, end_date=today + timedelta(days=30),
            next_billing_date=today + timedelta(days=30), auto_renew=True,
            stripe_customer_id="cus_yc5", stripe_subscription_id="sub_yc5",
            stripe_verified_at=timezone.now(), stripe_last_paid_event_id="evt_yc5",
            stripe_verified_price_id="price_yc5",
        )
        self.channel = WhatsAppNumber.objects.create(
            company=self.company, warehouse=self.warehouse, channel_name="Ventas YC5",
            display_number="+525500001234", provider=WhatsAppNumber.PROVIDER_YCLOUD,
            activation_status=WhatsAppNumber.STATUS_PENDING_ACTIVATION,
        )
        self.admin = get_user_model().objects.create_superuser("owner5", "owner5@example.com", "pass")

    @patch("messaging.ycloud_api.requests.get")
    def test_retrieve_uses_central_key_and_official_endpoint(self, mock_get):
        response = MagicMock(ok=True, status_code=200)
        response.json.return_value = {"phoneNumber": "+525500001234", "status": "CONNECTED"}
        mock_get.return_value = response
        retrieve_ycloud_phone_number(waba_id="waba-123", phone_number="55 0000 1234")
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["headers"]["X-API-Key"], "secret")
        self.assertIn("/whatsapp/phoneNumbers/waba-123/+5500001234", mock_get.call_args.args[0])

    def test_invalid_waba_is_rejected_without_http(self):
        with patch("messaging.ycloud_api.requests.get") as mock_get:
            with self.assertRaises(YCloudWhatsAppConfigurationError):
                retrieve_ycloud_phone_number(waba_id="../bad", phone_number="+525500001234")
            mock_get.assert_not_called()

    @patch("messaging.ycloud_onboarding.retrieve_ycloud_phone_number")
    def test_connected_number_is_activated(self, remote):
        remote.return_value = {"phoneNumber": "+525500001234", "phoneNumberId": "pn-123", "wabaId": "waba-123", "status": "CONNECTED"}
        result = verify_and_activate_ycloud_channel(channel_id=self.channel.pk, waba_id="waba-123")
        self.assertTrue(result.is_ycloud_ready)
        self.assertEqual(result.activation_status, WhatsAppNumber.STATUS_ACTIVE)

    @patch("messaging.ycloud_onboarding.retrieve_ycloud_phone_number")
    def test_number_mismatch_never_activates(self, remote):
        remote.return_value = {"phoneNumber": "+525599999999", "phoneNumberId": "pn-x", "wabaId": "waba-123", "status": "CONNECTED"}
        with self.assertRaises(ValidationError):
            verify_and_activate_ycloud_channel(channel_id=self.channel.pk, waba_id="waba-123")
        self.channel.refresh_from_db(); self.assertFalse(self.channel.is_active)

    @patch("messaging.ycloud_onboarding.retrieve_ycloud_phone_number")
    def test_waba_mismatch_never_activates(self, remote):
        remote.return_value = {"phoneNumber": "+525500001234", "phoneNumberId": "pn-x", "wabaId": "other", "status": "CONNECTED"}
        with self.assertRaises(ValidationError):
            verify_and_activate_ycloud_channel(channel_id=self.channel.pk, waba_id="waba-123")

    @patch("messaging.ycloud_onboarding.retrieve_ycloud_phone_number")
    def test_pending_remote_status_does_not_activate(self, remote):
        remote.return_value = {"phoneNumber": "+525500001234", "wabaId": "waba-123", "status": "PENDING"}
        with self.assertRaises(ValidationError):
            verify_and_activate_ycloud_channel(channel_id=self.channel.pk, waba_id="waba-123")
        self.channel.refresh_from_db(); self.assertFalse(self.channel.is_active)

    @patch("messaging.ycloud_onboarding.retrieve_ycloud_phone_number")
    def test_inactive_warehouse_blocks_api_call(self, remote):
        self.warehouse.is_active = False; self.warehouse.save(update_fields=["is_active"])
        with self.assertRaises(ValidationError):
            verify_and_activate_ycloud_channel(channel_id=self.channel.pk, waba_id="waba-123")
        remote.assert_not_called()

    def test_verification_view_requires_platform_owner(self):
        url = reverse("messaging:owner_ycloud_verify_channel", args=[self.channel.pk])
        response = self.client.post(url, {"waba_id": "waba-123"})
        self.assertEqual(response.status_code, 302)

    @patch("messaging.ycloud_onboarding.retrieve_ycloud_phone_number")
    def test_platform_owner_can_verify(self, remote):
        remote.return_value = {"phoneNumber": "+525500001234", "phoneNumberId": "pn-123", "wabaId": "waba-123", "status": "CONNECTED"}
        self.client.force_login(self.admin)
        response = self.client.post(reverse("messaging:owner_ycloud_verify_channel", args=[self.channel.pk]), {"waba_id": "waba-123"})
        self.assertRedirects(response, reverse("messaging:owner_saas_console"))
        self.channel.refresh_from_db(); self.assertTrue(self.channel.is_active)

    def test_ycloud_readiness_requires_remote_identity(self):
        self.channel.ycloud_enabled = True
        self.assertFalse(self.channel.is_ycloud_ready)
