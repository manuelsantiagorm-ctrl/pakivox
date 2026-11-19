from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
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
from shipping_hub.services.batch_rejection import report_rejected_packages
from warehouse.models import (
    OrderFulfillment,
    PackingOrder,
    PickingOrder,
    ShippingOrder,
)

User = get_user_model()


class BatchRejectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(
            name="Rejection Test Empresa", slug="rejection-test-empresa"
        )
        cls.warehouse = Warehouse.objects.create(
            company=cls.company, name="Rejection Bodega A1", code="REJ-A1"
        )
        cls.actor = User.objects.create_user(
            username="rejection-actor@test.local",
            email="rejection-actor@test.local",
            password="Rejection-runtime-only-123!",
        )
        cls.customer = Customer.objects.create(
            company=cls.company, full_name="Cliente Rejection"
        )
        cls.order = Order.objects.create(
            company=cls.company,
            customer=cls.customer,
            folio="REJECT-ORDER-001",
            status="paid",
            subtotal=Decimal("100.00"),
            discount=Decimal("0.00"),
            tax=Decimal("0.00"),
            shipping_cost=Decimal("0.00"),
            total=Decimal("100.00"),
            total_cost=Decimal("0.00"),
            gross_profit=Decimal("100.00"),
            margin_percent=Decimal("100.00"),
            amount_paid=Decimal("100.00"),
            balance_due=Decimal("0.00"),
            notes="Fixture de prueba - rechazo",
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
            package_count=1,
            weight_kg=Decimal("1.00"),
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
            master_tracking_number="REJECT-TRACK-001",
            status=CarrierShipment.STATUS_LABEL_READY,
        )
        cls.package = ShipmentPackage.objects.create(
            company=cls.company,
            carrier_shipment=cls.shipment,
            package_number=1,
            description="Caja única",
            weight_kg=Decimal("1.000"),
            length_cm=Decimal("10.00"),
            width_cm=Decimal("10.00"),
            height_cm=Decimal("10.00"),
            status=ShipmentPackage.STATUS_LABEL_READY,
        )
        cls.collection_point = ShipmentCollectionPoint.objects.create(
            company=cls.company,
            warehouse=cls.warehouse,
            name="Salida Rejection",
            created_by=cls.actor,
        )

    def setUp(self):
        self.batch = ShipmentBatch.objects.create(
            company=self.company,
            collection_point=self.collection_point,
            assigned_to=self.actor,
            created_by=self.actor,
        )
        self.item = ShipmentBatchItem.objects.create(
            company=self.company,
            batch=self.batch,
            package=self.package,
            carrier_name_snapshot="Estafeta",
        )

    def test_reporting_rejection_creates_held_incident(self):
        result = report_rejected_packages(
            company_id=self.company.pk,
            batch_id=self.batch.pk,
            item_ids=[self.item.pk],
            reason="Empaque dañado, no cumple normas",
            actor=self.actor,
        )

        self.assertEqual(result.reported_item_ids, [self.item.pk])
        self.assertEqual(len(result.incident_ids), 1)

        self.item.refresh_from_db()
        self.assertEqual(self.item.status, ShipmentBatchItem.STATUS_REJECTED)
        self.assertIsNotNone(self.item.incident_id)

        incident = LogisticsIncident.objects.get(pk=result.incident_ids[0])
        self.assertEqual(incident.incident_type, LogisticsIncident.TYPE_HELD)
        self.assertIn("Empaque dañado", incident.description)

        events = incident.events.all()
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().event_type, "rejected_at_carrier_counter")

    def test_rejection_without_reason_fails(self):
        with self.assertRaises(ValidationError):
            report_rejected_packages(
                company_id=self.company.pk,
                batch_id=self.batch.pk,
                item_ids=[self.item.pk],
                reason="   ",
                actor=self.actor,
            )

    def test_batch_completes_when_only_item_rejected(self):
        report_rejected_packages(
            company_id=self.company.pk,
            batch_id=self.batch.pk,
            item_ids=[self.item.pk],
            reason="Dimensiones fuera de norma",
            actor=self.actor,
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, ShipmentBatch.STATUS_COMPLETED)
