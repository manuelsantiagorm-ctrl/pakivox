from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from unittest.mock import patch

from accounts.models import UserProfile
from companies.models import Company
from inventory.models import (
    LotBalance,
    LotMovement,
    Product,
    ProductLot,
    ProductVariant,
    Stock,
    Warehouse,
)
from warehouse.models import VariantLocation, WarehouseLocation
from warehouse.views import ubicar_mercancia


User = get_user_model()


class LotPutawayLT1BTests(TestCase):

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_superuser(
            "lt1b-putaway@test.local",
            password="x",
        )
        self.company = Company.objects.create(
            name="LT1B Putaway",
            slug="lt1b-putaway",
        )
        # capability_required() exige un UserProfile activo ligado
        # a una empresa incluso para superusuarios. No se hace bypass
        # del gateway de seguridad en estas pruebas.
        UserProfile.objects.create(
            user=self.user,
            company=self.company,
            role="owner",
            is_active=True,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="Bodega Putaway",
            code="LT1B-P",
        )
        self.product = Product.objects.create(
            company=self.company,
            name="Producto Putaway",
            base_sku="PUT",
        )
        self.variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="PUT-SKU",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT,
        )
        self.staging = WarehouseLocation.objects.create(
            company=self.company,
            warehouse=self.warehouse,
            aisle="STAGING",
            rack="1",
            level="",
            bin_code="STAGING-LT1B",
            status=WarehouseLocation.STATUS_STAGING,
            is_virtual=True,
            is_active=True,
        )
        self.final = WarehouseLocation.objects.create(
            company=self.company,
            warehouse=self.warehouse,
            aisle="A",
            rack="1",
            level="1",
            bin_code="A1-LT1B",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )
        self.source = VariantLocation.objects.create(
            company=self.company,
            variant=self.variant,
            location=self.staging,
            quantity=10,
        )
        Stock.objects.create(
            company=self.company,
            warehouse=self.warehouse,
            variant=self.variant,
            physical_stock=10,
            reserved_stock=0,
            committed_stock=0,
        )
        self.lot = ProductLot.objects.create(
            company=self.company,
            variant=self.variant,
            lot_number="PUT-LOT",
        )
        self.source_balance = LotBalance.objects.create(
            company=self.company,
            lot=self.lot,
            location=self.staging,
            status=LotBalance.STATUS_AVAILABLE,
            physical_quantity=10,
            reserved_quantity=0,
            committed_quantity=0,
        )

    def _request(self, data):
        request = self.factory.post("/warehouse/ubicar-mercancia/", data=data)
        request.user = self.user
        request.session = {}
        setattr(request, "_messages", FallbackStorage(request))
        return request

    @patch("warehouse.views.require_active_warehouse")
    @patch("warehouse.views.get_user_company")
    def test_controlled_putaway_moves_variant_location_and_lot_balance(
        self,
        mocked_company,
        mocked_warehouse,
    ):
        mocked_company.return_value = self.company
        mocked_warehouse.return_value = self.warehouse

        request = self._request(
            {
                "staging_assignment_id": str(self.source.id),
                "final_location_id": str(self.final.id),
                "quantity": "4",
                "lot_balance_id": str(self.source_balance.id),
                "request_id": "a" * 32,
            }
        )

        response = ubicar_mercancia(request)
        self.assertEqual(response.status_code, 302)

        self.source.refresh_from_db()
        destination = VariantLocation.objects.get(
            company=self.company,
            variant=self.variant,
            location=self.final,
        )
        self.source_balance.refresh_from_db()
        destination_balance = LotBalance.objects.get(
            company=self.company,
            lot=self.lot,
            location=self.final,
        )
        stock = Stock.objects.get(
            company=self.company,
            warehouse=self.warehouse,
            variant=self.variant,
        )

        self.assertEqual(self.source.quantity, 6)
        self.assertEqual(destination.quantity, 4)
        self.assertEqual(self.source_balance.physical_quantity, 6)
        self.assertEqual(destination_balance.physical_quantity, 4)
        self.assertEqual(stock.physical_stock, 10)
        self.assertEqual(
            LotMovement.objects.filter(
                company=self.company,
                lot=self.lot,
                reference_type="putaway",
            ).count(),
            2,
        )

    @patch("warehouse.views.require_active_warehouse")
    @patch("warehouse.views.get_user_company")
    def test_controlled_putaway_rejects_quantity_above_selected_lot(
        self,
        mocked_company,
        mocked_warehouse,
    ):
        mocked_company.return_value = self.company
        mocked_warehouse.return_value = self.warehouse

        other_lot = ProductLot.objects.create(
            company=self.company,
            variant=self.variant,
            lot_number="PUT-LOT-2",
        )
        self.source_balance.physical_quantity = 4
        self.source_balance.save(update_fields=["physical_quantity"])
        LotBalance.objects.create(
            company=self.company,
            lot=other_lot,
            location=self.staging,
            physical_quantity=6,
        )

        request = self._request(
            {
                "staging_assignment_id": str(self.source.id),
                "final_location_id": str(self.final.id),
                "quantity": "5",
                "lot_balance_id": str(self.source_balance.id),
                "request_id": "b" * 32,
            }
        )

        response = ubicar_mercancia(request)
        self.assertEqual(response.status_code, 302)

        self.source.refresh_from_db()
        self.source_balance.refresh_from_db()
        self.assertEqual(self.source.quantity, 10)
        self.assertEqual(self.source_balance.physical_quantity, 4)
        self.assertFalse(
            VariantLocation.objects.filter(
                company=self.company,
                variant=self.variant,
                location=self.final,
            ).exists()
        )

    @patch("warehouse.views.require_active_warehouse")
    @patch("warehouse.views.get_user_company")
    def test_duplicate_putaway_request_is_idempotent(
        self,
        mocked_company,
        mocked_warehouse,
    ):
        mocked_company.return_value = self.company
        mocked_warehouse.return_value = self.warehouse

        data = {
            "staging_assignment_id": str(self.source.id),
            "final_location_id": str(self.final.id),
            "quantity": "2",
            "lot_balance_id": str(self.source_balance.id),
            "request_id": "c" * 32,
        }

        first = ubicar_mercancia(self._request(data))
        second = ubicar_mercancia(self._request(data))

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)

        self.source.refresh_from_db()
        self.source_balance.refresh_from_db()

        self.assertEqual(self.source.quantity, 8)
        self.assertEqual(self.source_balance.physical_quantity, 8)
        self.assertEqual(
            LotMovement.objects.filter(
                company=self.company,
                event_key="putaway:" + ("c" * 32) + ":out",
            ).count(),
            1,
        )
