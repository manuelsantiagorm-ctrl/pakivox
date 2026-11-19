from pathlib import Path

from django.test import SimpleTestCase


class SafeRequoteHistoryContractTests(SimpleTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.views = (
            root / "shipping_hub" / "views.py"
        ).read_text(encoding="utf-8")

    def test_purchase_linked_quotes_are_preserved(self):
        self.assertIn("protected_quote_ids = set(", self.views)
        self.assertIn(
            "ShippingPurchase.objects.for_company(company)",
            self.views,
        )
        self.assertIn(
            "selected_quote_id__in=existing_quote_ids",
            self.views,
        )
        self.assertIn("removable_quotes.delete()", self.views)

    def test_blanket_delete_is_absent(self):
        unsafe = """ShippingQuote.objects.filter(
                company=company,
                order=locked_order,
                shipping_order=(cubication.shipping_order),
            ).delete()"""
        self.assertNotIn(unsafe, self.views)

    def test_historical_quotes_are_hidden_from_current_comparison(self):
        self.assertGreaterEqual(
            self.views.count("purchases__isnull=True"),
            2,
        )

    def test_stale_post_cannot_select_historical_quote(self):
        self.assertIn("quote_is_current = (", self.views)
        self.assertIn(
            "La cotización ya no pertenece al lote vigente.",
            self.views,
        )

    def test_company_and_shipping_order_scope_remain(self):
        self.assertIn(
            "ShippingQuote.objects.for_company(company)",
            self.views,
        )
        self.assertIn(
            "shipping_order=cubication.shipping_order",
            self.views,
        )
