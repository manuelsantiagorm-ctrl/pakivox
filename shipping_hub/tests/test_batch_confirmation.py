from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from companies.models import Company
from crm.models import Customer
from inventory.models import Warehouse
from sales.models import Order
from shipping_hub.models import (
    CarrierShipment,
    CompanyShippingIntegration,
    ShipmentBatch,
    ShipmentBatchItem,
    ShipmentCollectionPoint,
    ShipmentPackage,
)
from shipping_hub.services.batch_confirmation import confirm_batch_items_delivered
from warehouse.models import (
    OrderFulfillment,
    PackingOrder,
    PickingOrder,
    ShippingOrder,
)

User = get_user_model()


class BatchConfirmationMultiPackageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(
            name="Batch Test Empresa",
            slug="batch-test-empresa",
        )
        cls.warehouse = Warehouse.objects.create(
            company=cls.company,
            name="Batch Bodega A1",
            code="BATCH-A1",
        )
        cls.actor = User.objects.create_user(
            username="batch-actor@test.local",
            email="batch-actor@test.local",
            password="Batch-runtime-only-123!",
        )

        cls.customer = Customer.objects.create(
            company=cls.company,
            full_name="Cliente Batch 2 cajas",
        )
        cls.order = Order.objects.create(
            company=cls.company,
            customer=cls.customer,
            folio="BATCH-ORDER-001",
            status="paid",
            subtotal=Decimal("200.00"),
            discount=Decimal("0.00"),
            tax=Decimal("0.00"),
            shipping_cost=Decimal("0.00"),
            total=Decimal("200.00"),
            total_cost=Decimal("0.00"),
            gross_profit=Decimal("200.00"),
            margin_percent=Decimal("100.00"),
            amount_paid=Decimal("200.00"),
            balance_due=Decimal("0.00"),
            notes="Fixture de prueba - lote 2 cajas",
        )
        cls.fulfillment = OrderFulfillment.objects.create(
            company=cls.company,
            order=cls.order,
            warehouse=cls.warehouse,
            sequence=1,
            status="pending",
        )
        cls.picking = PickingOrder.objects.create(
            company=cls.company,
            order=cls.order,
            fulfillment=cls.fulfillment,
            warehouse=cls.warehouse,
            status=PickingOrder.STATUS_COMPLETED,
        )
        cls.packing = PackingOrder.objects.create(
            company=cls.company,
            order=cls.order,
            fulfillment=cls.fulfillment,
            picking_order=cls.picking,
            status=PackingOrder.STATUS_PACKED,
            package_count=2,
            weight_kg=Decimal("2.00"),
        )
        cls.shipping_order = ShippingOrder.objects.create(
            company=cls.company,
            order=cls.order,
            fulfillment=cls.fulfillment,
            packing_order=cls.packing,
            carrier="Estafeta",
            status=ShippingOrder.STATUS_READY,
        )
        cls.shipment = CarrierShipment.objects.create(
            company=cls.company,
            shipping_order=cls.shipping_order,
            source=CarrierShipment.SOURCE_EXTERNAL,
            provider=CompanyShippingIntegration.PROVIDER_ENVIA,
            carrier_code="estafeta",
            carrier_name="Estafeta",
            master_tracking_number="BATCH-TRACK-001",
            status=CarrierShipment.STATUS_LABEL_READY,
        )

        # Pedido dividido en 2 cajas (mismo CarrierShipment, mismo cliente)
        cls.package_1 = ShipmentPackage.objects.create(
            company=cls.company,
            carrier_shipment=cls.shipment,
            package_number=1,
            description="Caja 1 de 2",
            weight_kg=Decimal("1.000"),
            length_cm=Decimal("10.00"),
            width_cm=Decimal("10.00"),
            height_cm=Decimal("10.00"),
            status=ShipmentPackage.STATUS_LABEL_READY,
        )
        cls.package_2 = ShipmentPackage.objects.create(
            company=cls.company,
            carrier_shipment=cls.shipment,
            package_number=2,
            description="Caja 2 de 2",
            weight_kg=Decimal("1.000"),
            length_cm=Decimal("10.00"),
            width_cm=Decimal("10.00"),
            height_cm=Decimal("10.00"),
            status=ShipmentPackage.STATUS_LABEL_READY,
        )

        cls.collection_point = ShipmentCollectionPoint.objects.create(
            company=cls.company,
            warehouse=cls.warehouse,
            name="Salida A",
            created_by=cls.actor,
        )

    def setUp(self):
        self.batch = ShipmentBatch.objects.create(
            company=self.company,
            collection_point=self.collection_point,
            assigned_to=self.actor,
            created_by=self.actor,
        )
        self.item_1 = ShipmentBatchItem.objects.create(
            company=self.company,
            batch=self.batch,
            package=self.package_1,
            carrier_name_snapshot="Estafeta",
        )
        self.item_2 = ShipmentBatchItem.objects.create(
            company=self.company,
            batch=self.batch,
            package=self.package_2,
            carrier_name_snapshot="Estafeta",
        )

    @patch("shipping_hub.services.dispatch_flow.transaction.on_commit")
    def test_no_dispatch_message_until_all_packages_confirmed(self, mock_on_commit):
        # Confirma SOLO la caja 1 de 2
        result = confirm_batch_items_delivered(
            company_id=self.company.pk,
            batch_id=self.batch.pk,
            item_ids=[self.item_1.pk],
            actor=self.actor,
        )

        self.assertEqual(result.confirmed_item_ids, [self.item_1.pk])
        self.assertEqual(result.dispatched_shipment_ids, [])
        self.assertIn(self.shipment.pk, result.pending_shipment_ids)

        # El WhatsApp de "pedido enviado" NUNCA debió dispararse todavía
        mock_on_commit.assert_not_called()

        self.shipment.refresh_from_db()
        self.assertNotEqual(self.shipment.status, CarrierShipment.STATUS_SHIPPED)

        # Ahora confirma la caja 2 (la que faltaba)
        result_2 = confirm_batch_items_delivered(
            company_id=self.company.pk,
            batch_id=self.batch.pk,
            item_ids=[self.item_2.pk],
            actor=self.actor,
        )

        self.assertEqual(result_2.confirmed_item_ids, [self.item_2.pk])
        self.assertIn(self.shipment.pk, result_2.dispatched_shipment_ids)

        # Hasta AHORA sí debió dispararse el mensaje
        mock_on_commit.assert_called()

        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, CarrierShipment.STATUS_SHIPPED)

    def test_confirming_same_item_twice_is_idempotent(self):
        confirm_batch_items_delivered(
            company_id=self.company.pk,
            batch_id=self.batch.pk,
            item_ids=[self.item_1.pk],
            actor=self.actor,
        )
        first_delivered_at = ShipmentBatchItem.objects.get(
            pk=self.item_1.pk
        ).delivered_at

        result = confirm_batch_items_delivered(
            company_id=self.company.pk,
            batch_id=self.batch.pk,
            item_ids=[self.item_1.pk],
            actor=self.actor,
        )

        second_delivered_at = ShipmentBatchItem.objects.get(
            pk=self.item_1.pk
        ).delivered_at
        self.assertEqual(first_delivered_at, second_delivered_at)
        self.assertEqual(result.pending_shipment_ids, [self.shipment.pk])

    def test_batch_marked_completed_when_all_items_resolved(self):
        confirm_batch_items_delivered(
            company_id=self.company.pk,
            batch_id=self.batch.pk,
            item_ids=[self.item_1.pk, self.item_2.pk],
            actor=self.actor,
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, ShipmentBatch.STATUS_COMPLETED)
        self.assertIsNotNone(self.batch.completed_at)
