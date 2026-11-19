from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from shipping_hub.models import CarrierShipment
from shipping_hub.services.envia import EnviaAPIError, EnviaClient
from shipping_hub.services.purchase_confirmation import (
    build_purchase_confirmation,
    validate_purchase_confirmation,
)


class FakeResponse:
    status_code = 422
    text = ""

    def __init__(self, message):
        self.message = message

    def json(self):
        return {"message": self.message}


class FakeSession:
    def __init__(self, response):
        self.response = response

    def request(self, **kwargs):
        return self.response


class EnviaOwnerPhase4Tests(SimpleTestCase):
    def setUp(self):
        self.company = SimpleNamespace(pk=11)
        self.shipment = SimpleNamespace(pk=22)
        self.quote = SimpleNamespace(
            pk=33, amount=Decimal("219.90"), currency="mxn", expires_at=None
        )
        self.integration = SimpleNamespace(
            pk=44, provider="envia", environment="production"
        )

    def token(self):
        return build_purchase_confirmation(
            company=self.company,
            shipment=self.shipment,
            quote=self.quote,
            integration=self.integration,
        )

    def validate(self, token=None, **overrides):
        values = {
            "company": self.company,
            "shipment": self.shipment,
            "quote": self.quote,
            "integration": self.integration,
        }
        values.update(overrides)
        return validate_purchase_confirmation(token or self.token(), **values)

    def test_signed_confirmation_accepts_exact_purchase(self):
        payload = self.validate()
        self.assertEqual(payload["amount"], "219.90")
        self.assertEqual(payload["currency"], "MXN")

    def test_missing_confirmation_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_purchase_confirmation(
                "", company=self.company, shipment=self.shipment,
                quote=self.quote, integration=self.integration,
            )

    def test_tampered_confirmation_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.validate(self.token() + "x")

    def test_changed_price_is_rejected(self):
        changed = SimpleNamespace(**{**self.quote.__dict__, "amount": Decimal("220.00")})
        with self.assertRaises(ValidationError):
            self.validate(quote=changed)

    def test_foreign_company_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.validate(company=SimpleNamespace(pk=99))

    def test_changed_environment_is_rejected(self):
        changed = SimpleNamespace(pk=44, provider="envia", environment="sandbox")
        with self.assertRaises(ValidationError):
            self.validate(integration=changed)

    def test_insufficient_balance_has_specific_code(self):
        client = EnviaClient("secret-token")
        with self.assertRaises(EnviaAPIError) as caught:
            client._request(
                "POST", "https://example.invalid",
                session=FakeSession(FakeResponse("Saldo insuficiente")),
            )
        self.assertEqual(caught.exception.error_code, "insufficient_balance")
        self.assertFalse(caught.exception.is_temporary)

    def test_generic_validation_error_is_not_balance_error(self):
        client = EnviaClient("secret-token")
        with self.assertRaises(EnviaAPIError) as caught:
            client._request(
                "POST", "https://example.invalid",
                session=FakeSession(FakeResponse("Código postal inválido")),
            )
        self.assertEqual(caught.exception.error_code, "provider_validation_error")

    def test_purchase_snapshot_fields_exist(self):
        fields = {field.name for field in CarrierShipment._meta.fields}
        self.assertTrue({
            "purchase_quote_amount", "purchase_currency", "purchase_provider",
            "purchase_environment", "purchase_confirmed_at",
            "purchase_confirmed_by", "purchase_started_at",
        } <= fields)

    def test_idempotency_key_remains_unique(self):
        field = CarrierShipment._meta.get_field("idempotency_key")
        self.assertTrue(field.unique)
