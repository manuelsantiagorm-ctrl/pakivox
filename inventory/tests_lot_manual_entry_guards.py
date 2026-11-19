from django.test import TestCase

from companies.models import Company
from inventory.forms import ManualStockEntryForm
from inventory.models import Product, ProductVariant


class LotManualEntryGuardTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Empresa LT1B Manual Guard",
            slug="empresa-lt1b-manual-guard",
        )
        self.product = Product.objects.create(
            company=self.company,
            name="Producto LT1B Manual Guard",
            base_sku="LT1B-MANUAL",
        )
        self.normal = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="LT1B-MANUAL-NORMAL",
            inventory_control=ProductVariant.INVENTORY_CONTROL_NORMAL,
        )
        self.controlled = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="LT1B-MANUAL-LOT",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT,
        )

    def test_manual_entry_form_only_offers_normal_variants(self):
        form = ManualStockEntryForm(
            company=self.company,
            product=self.product,
        )

        ids = set(
            form.fields["variant"].queryset.values_list("id", flat=True)
        )

        self.assertIn(self.normal.pk, ids)
        self.assertNotIn(self.controlled.pk, ids)

    def test_manipulated_post_cannot_select_controlled_variant(self):
        form = ManualStockEntryForm(
            data={
                "variant": str(self.controlled.pk),
                "quantity": "5",
                "reference": "Intento LT1B",
                "notes": "",
            },
            company=self.company,
            product=self.product,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("variant", form.errors)
