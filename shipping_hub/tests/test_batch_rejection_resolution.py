from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from companies.models import Company
from crm.models import Customer
from inventory.models import Warehouse
from sales.models import Order
from shipping_hub.models import (
    CarrierShipment,
    CompanyShippingIntegration,
    IncidentExpenseReceipt,
    LogisticsIncident,
    ShipmentBatch,
    ShipmentBatchItem,
    ShipmentCollectionPoint,
    ShipmentPackage,
)
from shipping_hub.services.batch_rejection import report_rejected_packages
from shipping_hub.services.batch_rejection_resolution import (
    resolve_rejection_by_cancellation,
    resolve_rejection_by_field_expense,
    resolve_rejection_by_new_label,
    resolve_rejection_by_repack,
)
from warehouse.models import (
    OrderFulfillment,
    PackingOrder,
    PickingOrder,
    ShippingOrder,
)

User = get_user_model()


class BatchRejectionResolutionTestsBase(TestCase):
    """Base con una cadena completa nueva por cada subclase de caso."""

    ORDER_FOLIO = "RESOLVE-ORDER"

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(
            name=f"Resolve Test {cls.__name__}",
            slug=f"resolve-test-{cls.__name__.lower()}",
        )
        cls.warehouse = Warehouse.objects.create(
            company=cls.company,
            name="Resolve Bodega",
            code=f"RES-{cls.__name__[:6]}",
        )
        cls.actor = User.objects.create_user(
            username=f"resolve-actor-{cls.__name__.lower()}@test.local",
            email=f"resolve-actor-{cls.__name__.lower()}@test.local",
            password="Resolve-runtime-only-123!",
        )
        cls.customer = Customer.objects.create(
            company=cls.company,
            full_name="Cliente Resolve",
        )
        cls.order = Order.objects.create(
            company=cls.company,
            customer=cls.customer,
            folio=f"{cls.ORDER_FOLIO}-{cls.__name__}",
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
            notes="Fixture de prueba - resolucion de rechazo",
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
            master_tracking_number="RESOLVE-TRACK-001",
            status=CarrierShipment.STATUS_LABEL_READY,
        )
        cls.package = ShipmentPackage.objects.create(
            company=cls.company,
            carrier_shipment=cls.shipment,
            package_number=1,
            description="Caja unica",
            weight_kg=Decimal("1.000"),
            length_cm=Decimal("10.00"),
            width_cm=Decimal("10.00"),
            height_cm=Decimal("10.00"),
            status=ShipmentPackage.STATUS_LABEL_READY,
        )
        cls.collection_point = ShipmentCollectionPoint.objects.create(
            company=cls.company,
            warehouse=cls.warehouse,
            name="Salida Resolve",
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
        result = report_rejected_packages(
            company_id=self.company.pk,
            batch_id=self.batch.pk,
            item_ids=[self.item.pk],
            reason="Motivo inicial de prueba",
            actor=self.actor,
        )
        self.incident_id = result.incident_ids[0]


class RejectionRepackResolutionTests(BatchRejectionResolutionTestsBase):
    def test_repack_resolves_incident_without_touching_stock_or_label(self):
        original_tracking = self.shipment.master_tracking_number

        incident = resolve_rejection_by_repack(
            company_id=self.company.pk,
            incident_id=self.incident_id,
            notes="Se reforzo el empaque y Estafeta lo acepto.",
            actor=self.actor,
        )

        self.assertEqual(incident.status, LogisticsIncident.STATUS_RESOLVED)
        self.assertIsNotNone(incident.resolved_at)

        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.master_tracking_number, original_tracking)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "paid")

    def test_repack_is_idempotent(self):
        resolve_rejection_by_repack(
            company_id=self.company.pk,
            incident_id=self.incident_id,
            notes="x",
            actor=self.actor,
        )
        incident = resolve_rejection_by_repack(
            company_id=self.company.pk,
            incident_id=self.incident_id,
            notes="y",
            actor=self.actor,
        )
        self.assertEqual(incident.status, LogisticsIncident.STATUS_RESOLVED)


class RejectionNewLabelResolutionTests(BatchRejectionResolutionTestsBase):
    def test_new_label_updates_shipment_and_package_without_stock(self):
        incident = resolve_rejection_by_new_label(
            company_id=self.company.pk,
            incident_id=self.incident_id,
            new_carrier_name="FedEx",
            new_tracking_number="NEW-TRACK-999",
            notes="Cambio de paqueteria por rechazo de medidas.",
            actor=self.actor,
        )

        self.assertEqual(incident.status, LogisticsIncident.STATUS_RESOLVED)
        self.assertIn("RESOLVE-TRACK-001", incident.resolution)
        self.assertIn("NEW-TRACK-999", incident.resolution)

        self.shipment.refresh_from_db()
        self.package.refresh_from_db()
        self.assertEqual(self.shipment.carrier_name, "FedEx")
        self.assertEqual(self.shipment.master_tracking_number, "NEW-TRACK-999")
        self.assertEqual(self.package.tracking_number, "NEW-TRACK-999")

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "paid")

    def test_new_label_requires_carrier_and_tracking(self):
        with self.assertRaises(ValidationError):
            resolve_rejection_by_new_label(
                company_id=self.company.pk,
                incident_id=self.incident_id,
                new_carrier_name="",
                new_tracking_number="",
                notes="x",
                actor=self.actor,
            )


class RejectionFieldExpenseResolutionTests(BatchRejectionResolutionTestsBase):
    def _receipt_file(self, name="recibo.jpg"):
        return SimpleUploadedFile(
            name, b"contenido-falso-de-imagen", content_type="image/jpeg"
        )

    def test_field_expense_creates_pending_receipt_and_updates_label(self):
        incident, receipt = resolve_rejection_by_field_expense(
            company_id=self.company.pk,
            incident_id=self.incident_id,
            new_carrier_name="DHL",
            new_tracking_number="FIELD-TRACK-001",
            amount=Decimal("185.50"),
            currency="MXN",
            receipt_file=self._receipt_file(),
            actor=self.actor,
        )

        self.assertEqual(incident.status, LogisticsIncident.STATUS_RESOLVED)
        self.assertEqual(receipt.status, IncidentExpenseReceipt.STATUS_PENDING)
        self.assertEqual(receipt.amount, Decimal("185.50"))
        self.assertEqual(receipt.reported_by, self.actor)
        self.assertTrue(receipt.sha256)

        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.carrier_name, "DHL")

    def test_field_expense_requires_receipt_file(self):
        with self.assertRaises(ValidationError):
            resolve_rejection_by_field_expense(
                company_id=self.company.pk,
                incident_id=self.incident_id,
                new_carrier_name="DHL",
                new_tracking_number="FIELD-TRACK-002",
                amount=Decimal("100.00"),
                currency="MXN",
                receipt_file=None,
                actor=self.actor,
            )

    def test_field_expense_requires_positive_amount(self):
        with self.assertRaises(ValidationError):
            resolve_rejection_by_field_expense(
                company_id=self.company.pk,
                incident_id=self.incident_id,
                new_carrier_name="DHL",
                new_tracking_number="FIELD-TRACK-003",
                amount=Decimal("0.00"),
                currency="MXN",
                receipt_file=self._receipt_file(),
                actor=self.actor,
            )

    def test_uploading_same_file_twice_is_idempotent(self):
        file_1 = self._receipt_file()
        incident, receipt_1 = resolve_rejection_by_field_expense(
            company_id=self.company.pk,
            incident_id=self.incident_id,
            new_carrier_name="DHL",
            new_tracking_number="FIELD-TRACK-004",
            amount=Decimal("100.00"),
            currency="MXN",
            receipt_file=file_1,
            actor=self.actor,
        )
        file_2 = self._receipt_file()
        incident_2, receipt_2 = resolve_rejection_by_field_expense(
            company_id=self.company.pk,
            incident_id=self.incident_id,
            new_carrier_name="DHL",
            new_tracking_number="FIELD-TRACK-004",
            amount=Decimal("100.00"),
            currency="MXN",
            receipt_file=file_2,
            actor=self.actor,
        )
        self.assertEqual(receipt_1.pk, receipt_2.pk)
        self.assertEqual(
            IncidentExpenseReceipt.objects.filter(incident_id=self.incident_id).count(),
            1,
        )


class RejectionCancellationResolutionTests(BatchRejectionResolutionTestsBase):
    def test_cancellation_resolves_incident_and_cancels_order_flow(self):
        incident = resolve_rejection_by_cancellation(
            company_id=self.company.pk,
            incident_id=self.incident_id,
            notes="Empaque irreparable, no hay forma de enviarlo.",
            actor=self.actor,
        )

        self.assertEqual(incident.status, LogisticsIncident.STATUS_RESOLVED)

        self.picking.refresh_from_db()
        self.assertEqual(self.picking.status, PickingOrder.STATUS_CANCELLED)
