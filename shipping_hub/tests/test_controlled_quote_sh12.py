from decimal import Decimal

from django.core.management import CommandError
from django.test import SimpleTestCase

from shipping_hub.management.commands.quote_shipping_order import (
    CONFIRM_TEXT,
    _money,
)


class ControlledQuoteCommandTests(SimpleTestCase):
    def test_confirmation_is_exact_and_stable(self):
        self.assertEqual(
            CONFIRM_TEXT,
            "COTIZAR-SIN-COMPRAR",
        )

    def test_money_normalizes_decimal(self):
        self.assertEqual(
            _money("123.456"),
            Decimal("123.46"),
        )

    def test_money_rejects_invalid_value(self):
        with self.assertRaises(CommandError):
            _money("incorrecto")
