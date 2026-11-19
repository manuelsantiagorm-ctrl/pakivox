from django.test import SimpleTestCase

from shipping_hub.management.commands.recover_failed_not_charged_purchase import (
    CONFIRM_TEXT,
)


class RecoverFailedNotChargedPurchaseTests(SimpleTestCase):
    def test_confirmation_is_exact(self):
        self.assertEqual(
            CONFIRM_TEXT,
            "RECUPERAR-SIN-COBRO",
        )
