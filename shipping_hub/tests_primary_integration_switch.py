from cryptography.fernet import Fernet
from django.test import TestCase, override_settings

from companies.models import Company
from shipping_hub.forms import ShippingIntegrationForm
from shipping_hub.models import CompanyShippingIntegration
from shipping_hub.services.credentials import encrypt_credentials


@override_settings(SHIPPING_HUB_ENCRYPTION_KEY=Fernet.generate_key().decode("ascii"))
class PrimaryIntegrationSwitchTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Empresa A", slug="empresa-a")
        self.other_company = Company.objects.create(
            name="Empresa B", slug="empresa-b"
        )

    def _credentials(self):
        return encrypt_credentials(
            {"client_id": "client-test", "client_secret": "secret-test"}
        )

    def _integration(self, *, company=None, environment="production", primary=False):
        return CompanyShippingIntegration.objects.create(
            company=company or self.company,
            provider=CompanyShippingIntegration.PROVIDER_SKYDROPX,
            account_mode=CompanyShippingIntegration.ACCOUNT_MODE_COMPANY,
            environment=environment,
            account_name="Skydropx",
            preferred_label_format=CompanyShippingIntegration.FORMAT_PDF,
            fallback_label_format=CompanyShippingIntegration.FORMAT_ZPL,
            credentials_encrypted=self._credentials(),
            last_connection_test_success=True,
            is_active=True,
            is_primary=primary,
        )

    def _form_data(self, integration, *, active=True, primary=True):
        data = {
            "provider": integration.provider,
            "account_mode": integration.account_mode,
            "environment": integration.environment,
            "account_name": integration.account_name,
            "preferred_label_format": integration.preferred_label_format,
            "fallback_label_format": integration.fallback_label_format,
            "priority": integration.priority,
            "allow_quote_fallback": "on",
        }
        if active:
            data["is_active"] = "on"
        if primary:
            data["is_primary"] = "on"
        return data

    def test_selecting_new_primary_replaces_previous_automatically(self):
        previous = self._integration(primary=True)
        candidate = CompanyShippingIntegration.objects.create(
            company=self.company,
            provider=CompanyShippingIntegration.PROVIDER_ENVIA,
            account_mode=CompanyShippingIntegration.ACCOUNT_MODE_COMPANY,
            environment="production",
            account_name="Envia",
            preferred_label_format=CompanyShippingIntegration.FORMAT_PDF,
            fallback_label_format=CompanyShippingIntegration.FORMAT_ZPL,
            credentials_encrypted=encrypt_credentials({"api_token": "token-test"}),
            last_connection_test_success=True,
            is_active=True,
            is_primary=False,
        )
        form = ShippingIntegrationForm(
            self._form_data(candidate), instance=candidate, company=self.company
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        previous.refresh_from_db()
        self.assertTrue(saved.is_primary)
        self.assertFalse(previous.is_primary)

    def test_deactivating_integration_also_removes_primary_flag(self):
        integration = self._integration(primary=True)
        form = ShippingIntegrationForm(
            self._form_data(integration, active=False, primary=True),
            instance=integration,
            company=self.company,
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertFalse(saved.is_active)
        self.assertFalse(saved.is_primary)

    def test_primary_replacement_never_changes_another_company(self):
        other_primary = self._integration(company=self.other_company, primary=True)
        candidate = self._integration(primary=False)
        form = ShippingIntegrationForm(
            self._form_data(candidate), instance=candidate, company=self.company
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        other_primary.refresh_from_db()
        self.assertTrue(other_primary.is_primary)

    def test_primary_replacement_is_scoped_to_same_environment(self):
        sandbox_primary = self._integration(environment="sandbox", primary=True)
        candidate = self._integration(environment="production", primary=False)
        form = ShippingIntegrationForm(
            self._form_data(candidate), instance=candidate, company=self.company
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        sandbox_primary.refresh_from_db()
        self.assertTrue(sandbox_primary.is_primary)
