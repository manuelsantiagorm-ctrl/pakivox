from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from companies.models import Company
from crm.models import Customer
from inventory.models import Warehouse
from sales.models import Order
from shipping_hub.models import (
    CarrierShipment,
    CompanyShippingIntegration,
    LogisticsIncident,
    ShipmentBatch,
    ShipmentBatchItem,
    ShipmentCollectionPoint,
    ShipmentPackage,
)
from shipping_hub.services.batch_missing import report_missing_packages
from warehouse.models import (
    OrderFulfillment,
    PackingOrder,
    PickingOrder,
    ShippingOrder,
)

User = get_user_model()


class BatchMissingPackageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(
            name="Missing Test Empresa", slug="missing-test-empresa"
        )
        cls.warehouse = Warehouse.objects.create(
            company=cls.company, name="Missing Bodega A1", code="MISS-A1"
        )
        cls.actor = User.objects.create_user(
            username="missing-actor@test.local",
            email="missing-actor@test.local",
            password="Missing-runtime-only-123!",
        )
        cls.customer = Customer.objects.create(
            company=cls.company, full_name="Cliente Missing"
        )
        cls.order = Order.objects.create(
            company=cls.company,
            customer=cls.customer,
            folio="MISSING-ORDER-001",
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
            notes="Fixture de prueba - faltante",
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
            master_tracking_number="MISSING-TRACK-001",
            status=CarrierShipment.STATUS_LABEL_READY,
        )
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
            name="Salida Missing",
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

    def test_reporting_missing_package_creates_incident(self):
        result = report_missing_packages(
            company_id=self.company.pk,
            batch_id=self.batch.pk,
            item_ids=[self.item_1.pk],
            actor=self.actor,
        )

        self.assertEqual(result.reported_item_ids, [self.item_1.pk])
        self.assertEqual(len(result.incident_ids), 1)

        self.item_1.refresh_from_db()
        self.assertEqual(self.item_1.status, ShipmentBatchItem.STATUS_MISSING)
        self.assertIsNotNone(self.item_1.incident_id)

        incident = LogisticsIncident.objects.get(pk=result.incident_ids[0])
        self.assertEqual(incident.incident_type, LogisticsIncident.TYPE_LOST)
        self.assertEqual(incident.severity, LogisticsIncident.SEVERITY_HIGH)
        self.assertEqual(incident.status, LogisticsIncident.STATUS_OPEN)
        self.assertEqual(incident.warehouse_id, self.warehouse.pk)
        self.assertEqual(incident.package_id, self.package_1.pk)

        # El item 2 sigue intacto, no se tocó
        self.item_2.refresh_from_db()
        self.assertEqual(self.item_2.status, ShipmentBatchItem.STATUS_PENDING)

    def test_batch_not_completed_while_other_items_pending(self):
        report_missing_packages(
            company_id=self.company.pk,
            batch_id=self.batch.pk,
            item_ids=[self.item_1.pk],
            actor=self.actor,
        )
        self.batch.refresh_from_db()
        # Sigue abierto porque item_2 sigue pendiente
        self.assertEqual(self.batch.status, ShipmentBatch.STATUS_OPEN)

    def test_reporting_already_resolved_item_fails(self):
        report_missing_packages(
            company_id=self.company.pk,
            batch_id=self.batch.pk,
            item_ids=[self.item_1.pk],
            actor=self.actor,
        )
        with self.assertRaises(Exception):
            report_missing_packages(
                company_id=self.company.pk,
                batch_id=self.batch.pk,
                item_ids=[self.item_1.pk],
                actor=self.actor,
            )
