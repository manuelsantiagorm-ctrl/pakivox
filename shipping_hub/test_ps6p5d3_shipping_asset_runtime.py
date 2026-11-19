import hashlib
import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import (
    Capability,
    EmployeeCapabilityAssignment,
    UserProfile,
    UserWarehouseAccess,
)
from accounts.warehouse_access import SESSION_WAREHOUSE_KEY
from companies.models import Company
from crm.models import Customer
from inventory.models import Warehouse
from sales.models import Order
from shipping_hub.models import (
    CarrierShipment,
    CompanyShippingIntegration,
    DeliveryEvidence,
    DeliveryEvidenceAsset,
    ShipmentPackage,
    ShippingPrintItem,
    ShippingPrintJob,
)
from warehouse.models import (
    OrderFulfillment,
    PackingOrder,
    PickingOrder,
    ShippingOrder,
)


User = get_user_model()


class PS6P5D3ShippingAssetRuntimeTests(TestCase):
    """
    Prueba HTTP + PostgreSQL real para archivos sensibles de Shipping Hub.

    Contrato:
    - capability shipping.view;
    - empresa actual;
    - bodega autorizada;
    - parent/child exactos;
    - objeto de empresa extranjera no visible;
    - archivo entregado solamente por backend autorizado.
    """

    @classmethod
    def setUpClass(cls):
        cls._media_root = tempfile.mkdtemp(
            prefix="pakivox_ps6p5d3_shipping_"
        )

        cls._media_override = override_settings(
            MEDIA_ROOT=cls._media_root,
        )

        cls._media_override.enable()

        try:
            super().setUpClass()

        except Exception:
            cls._media_override.disable()
            shutil.rmtree(
                cls._media_root,
                ignore_errors=True,
            )
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()

        finally:
            cls._media_override.disable()

            shutil.rmtree(
                cls._media_root,
                ignore_errors=True,
            )

    @classmethod
    def setUpTestData(cls):
        if connection.vendor != "postgresql":
            raise AssertionError(
                "PS-6P.5D3B3C requiere PostgreSQL real; "
                f"vendor actual={connection.vendor}"
            )

        # ============================================================
        # EMPRESAS
        # ============================================================

        cls.company = Company.objects.create(
            name="D3 Shipping Empresa A",
            slug="d3-shipping-empresa-a",
        )

        cls.foreign_company = Company.objects.create(
            name="D3 Shipping Empresa B",
            slug="d3-shipping-empresa-b",
        )

        # ============================================================
        # USUARIOS
        # ============================================================

        cls.user = User.objects.create_user(
            username="d3-shipping-user@test.local",
            email="d3-shipping-user@test.local",
            password="D3-runtime-only-123!",
        )

        cls.no_cap_user = User.objects.create_user(
            username="d3-shipping-nocap@test.local",
            email="d3-shipping-nocap@test.local",
            password="D3-runtime-only-123!",
        )

        UserProfile.objects.create(
            user=cls.user,
            company=cls.company,
            role="warehouse",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )

        UserProfile.objects.create(
            user=cls.no_cap_user,
            company=cls.company,
            role="warehouse",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )

        # ============================================================
        # CAPABILITY
        # ============================================================

        shipping_view, _ = Capability.objects.get_or_create(
            code="shipping.view",
            defaults={
                "name": "Consultar envíos y tracking",
                "description": "",
                "area_code": "logistics_delivery",
                "is_active": True,
            },
        )

        EmployeeCapabilityAssignment.objects.create(
            company=cls.company,
            user=cls.user,
            capability=shipping_view,
            source=EmployeeCapabilityAssignment.SOURCE_DIRECT,
            is_active=True,
        )

        # ============================================================
        # BODEGAS
        # ============================================================

        cls.warehouse_a1 = Warehouse.objects.create(
            company=cls.company,
            name="D3 Bodega A1",
            code="D3-SHIP-A1",
        )

        cls.warehouse_a2 = Warehouse.objects.create(
            company=cls.company,
            name="D3 Bodega A2",
            code="D3-SHIP-A2",
        )

        cls.foreign_warehouse = Warehouse.objects.create(
            company=cls.foreign_company,
            name="D3 Bodega B1",
            code="D3-SHIP-B1",
        )

        # El usuario autorizado SOLO ve A1.
        UserWarehouseAccess.objects.create(
            company=cls.company,
            user=cls.user,
            warehouse=cls.warehouse_a1,
            access_level=UserWarehouseAccess.LEVEL_VIEW,
            is_active=True,
        )

        # Usuario sin capability, pero con bodega válida:
        # permite separar capability de warehouse.
        UserWarehouseAccess.objects.create(
            company=cls.company,
            user=cls.no_cap_user,
            warehouse=cls.warehouse_a1,
            access_level=UserWarehouseAccess.LEVEL_VIEW,
            is_active=True,
        )

        # ============================================================
        # CUATRO CADENAS REALES
        # ============================================================

        cls.primary = cls._make_chain(
            company=cls.company,
            warehouse=cls.warehouse_a1,
            tag="A1-PRIMARY",
        )

        cls.alternate = cls._make_chain(
            company=cls.company,
            warehouse=cls.warehouse_a1,
            tag="A1-ALT",
        )

        cls.wrong_warehouse = cls._make_chain(
            company=cls.company,
            warehouse=cls.warehouse_a2,
            tag="A2",
        )

        cls.foreign = cls._make_chain(
            company=cls.foreign_company,
            warehouse=cls.foreign_warehouse,
            tag="B1",
        )

    @classmethod
    def _make_chain(
        cls,
        *,
        company,
        warehouse,
        tag,
    ):
        customer = Customer.objects.create(
            company=company,
            full_name=f"Cliente D3 {tag}",
        )

        order = Order.objects.create(
            company=company,
            customer=customer,
            folio=f"D3-SHIP-{tag}",
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
            notes="PS-6P.5D3B3C runtime fixture",
        )

        fulfillment = OrderFulfillment.objects.create(
            company=company,
            order=order,
            warehouse=warehouse,
            sequence=1,
            status="pending",
        )

        picking = PickingOrder.objects.create(
            company=company,
            order=order,
            fulfillment=fulfillment,
            warehouse=warehouse,
            status=PickingOrder.STATUS_COMPLETED,
        )

        packing = PackingOrder.objects.create(
            company=company,
            order=order,
            fulfillment=fulfillment,
            picking_order=picking,
            status=PackingOrder.STATUS_PACKED,
            package_count=1,
            weight_kg=Decimal("1.00"),
        )

        shipping_order = ShippingOrder.objects.create(
            company=company,
            order=order,
            fulfillment=fulfillment,
            packing_order=packing,
            carrier="D3 Runtime Carrier",
            status=ShippingOrder.STATUS_READY,
        )

        shipment = CarrierShipment.objects.create(
            company=company,
            shipping_order=shipping_order,
            source=CarrierShipment.SOURCE_EXTERNAL,
            provider=CompanyShippingIntegration.PROVIDER_ENVIA,
            carrier_code="d3",
            carrier_name="D3 Runtime Carrier",
            status=CarrierShipment.STATUS_LABEL_READY,
        )

        package = ShipmentPackage.objects.create(
            company=company,
            carrier_shipment=shipment,
            package_number=1,
            description=f"Paquete {tag}",
            weight_kg=Decimal("1.000"),
            length_cm=Decimal("10.00"),
            width_cm=Decimal("10.00"),
            height_cm=Decimal("10.00"),
            label_format=ShipmentPackage.LABEL_FORMAT_PDF,
            status=ShipmentPackage.STATUS_LABEL_READY,
        )

        # External package label usa un prefijo contractual exacto.
        label_payload = f"D3-LABEL-{tag}".encode("utf-8")

        expected_label_name = (
            f"shipping_hub/company_{company.pk}/"
            f"external_packages/package_{package.pk}/"
            "label.pdf"
        )

        stored_label_name = default_storage.save(
            expected_label_name,
            ContentFile(label_payload),
        )

        if stored_label_name != expected_label_name:
            raise AssertionError(
                "Storage cambió inesperadamente el nombre de la guía: "
                f"{stored_label_name}"
            )

        package.label_file.name = stored_label_name
        package.external_label_original_name = f"guia-{tag}.pdf"
        package.external_label_content_type = "application/pdf"

        package.save(
            update_fields=[
                "label_file",
                "external_label_original_name",
                "external_label_content_type",
                "updated_at",
            ]
        )

        # ============================================================
        # DELIVERY EVIDENCE
        # ============================================================

        evidence = DeliveryEvidence.objects.create(
            company=company,
            carrier_shipment=shipment,
            warehouse=warehouse,
            result=DeliveryEvidence.RESULT_PARTIAL,
        )

        evidence_payload = f"D3-EVIDENCE-{tag}".encode("utf-8")

        asset = DeliveryEvidenceAsset(
            company=company,
            evidence=evidence,
            asset_type=DeliveryEvidenceAsset.TYPE_DOCUMENT,
            original_name=f"evidence-{tag}.txt",
            content_type="text/plain",
            size_bytes=len(evidence_payload),
            sha256=hashlib.sha256(
                evidence_payload
            ).hexdigest(),
        )

        asset.file.save(
            f"evidence-{tag}.txt",
            ContentFile(evidence_payload),
            save=False,
        )

        asset.save()

        # ============================================================
        # PRINT CENTER
        # ============================================================

        print_job = ShippingPrintJob.objects.create(
            company=company,
            requested_by=None,
            mode=ShippingPrintJob.MODE_SINGLE,
            status=ShippingPrintJob.STATUS_CREATED,
            warehouse_id_snapshot=warehouse.pk,
            reason="D3 runtime",
            item_count=1,
        )

        print_item = ShippingPrintItem.objects.create(
            company=company,
            job=print_job,
            package=package,
            carrier_shipment=shipment,
            sequence=1,
            label_format_snapshot=package.label_format,
            source_snapshot=shipment.source,
        )

        return {
            "customer": customer,
            "order": order,
            "fulfillment": fulfillment,
            "picking": picking,
            "packing": packing,
            "shipping_order": shipping_order,
            "shipment": shipment,
            "package": package,
            "label_payload": label_payload,
            "evidence": evidence,
            "asset": asset,
            "evidence_payload": evidence_payload,
            "print_job": print_job,
            "print_item": print_item,
        }

    def setUp(self):
        self._login_with_warehouse(
            self.user,
            self.warehouse_a1,
        )

    def _login_with_warehouse(
        self,
        user,
        warehouse,
    ):
        self.client.force_login(user)

        session = self.client.session
        session[SESSION_WAREHOUSE_KEY] = warehouse.pk
        session.save()

    def _stream_body(self, response):
        """
        Lee el archivo de un FileResponse sin consumir el wrapper
        streaming del Django test client.

        Consumir response.streaming_content hasta el final dispara
        response.close() -> request_finished -> cierre de conexiones.
        Dentro de TestCase eso puede inutilizar la conexión PostgreSQL
        que sigue participando en la transacción del test.

        Cerramos únicamente el file handle, no la respuesta HTTP.
        """
        file_obj = getattr(
            response,
            "file_to_stream",
            None,
        )

        if file_obj is None:
            if not getattr(response, "streaming", False):
                return response.content

            raise AssertionError(
                "Se esperaba FileResponse con file_to_stream."
            )

        try:
            return file_obj.read()

        finally:
            file_obj.close()

    # ================================================================
    # 1-4 DELIVERY EVIDENCE ASSET
    # ================================================================

    def test_01_delivery_evidence_authorized_returns_200(self):
        url = reverse(
            "shipping_hub:delivery_evidence_asset",
            kwargs={
                "evidence_id": self.primary["evidence"].pk,
                "asset_id": self.primary["asset"].pk,
            },
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertTrue(
            response.streaming,
            "El endpoint autorizado debe entregar una respuesta streaming.",
        )

    def test_02_delivery_evidence_parent_child_mismatch_is_404(self):
        url = reverse(
            "shipping_hub:delivery_evidence_asset",
            kwargs={
                "evidence_id": self.primary["evidence"].pk,
                "asset_id": self.alternate["asset"].pk,
            },
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            404,
        )

    def test_03_delivery_evidence_foreign_company_is_404(self):
        url = reverse(
            "shipping_hub:delivery_evidence_asset",
            kwargs={
                "evidence_id": self.foreign["evidence"].pk,
                "asset_id": self.foreign["asset"].pk,
            },
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            404,
        )

    def test_04_delivery_evidence_wrong_same_company_warehouse_is_404(self):
        url = reverse(
            "shipping_hub:delivery_evidence_asset",
            kwargs={
                "evidence_id": self.wrong_warehouse["evidence"].pk,
                "asset_id": self.wrong_warehouse["asset"].pk,
            },
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            404,
        )

    # ================================================================
    # 5-8 EXTERNAL PACKAGE LABEL
    # ================================================================

    def test_05_external_package_label_authorized_returns_200(self):
        url = reverse(
            "shipping_hub:external_package_label_file",
            kwargs={
                "shipping_id": self.primary["shipping_order"].pk,
                "package_id": self.primary["package"].pk,
            },
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertTrue(
            response.streaming,
            "El endpoint autorizado debe entregar una respuesta streaming.",
        )

    def test_06_external_package_parent_child_mismatch_is_404(self):
        url = reverse(
            "shipping_hub:external_package_label_file",
            kwargs={
                "shipping_id": self.primary["shipping_order"].pk,
                "package_id": self.alternate["package"].pk,
            },
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            404,
        )

    def test_07_external_package_foreign_company_is_404(self):
        url = reverse(
            "shipping_hub:external_package_label_file",
            kwargs={
                "shipping_id": self.foreign["shipping_order"].pk,
                "package_id": self.foreign["package"].pk,
            },
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            404,
        )

    def test_08_external_package_wrong_same_company_warehouse_is_403(self):
        url = reverse(
            "shipping_hub:external_package_label_file",
            kwargs={
                "shipping_id": self.wrong_warehouse["shipping_order"].pk,
                "package_id": self.wrong_warehouse["package"].pk,
            },
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            403,
        )

    # ================================================================
    # 9-12 PRINT ITEM ASSET
    # ================================================================

    def test_09_print_item_authorized_returns_200(self):
        url = reverse(
            "shipping_hub:print_item_asset",
            kwargs={
                "job_id": self.primary["print_job"].pk,
                "item_id": self.primary["print_item"].pk,
            },
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertTrue(
            response.streaming,
            "El endpoint autorizado debe entregar una respuesta streaming.",
        )

    def test_10_print_item_parent_child_mismatch_is_404(self):
        url = reverse(
            "shipping_hub:print_item_asset",
            kwargs={
                "job_id": self.primary["print_job"].pk,
                "item_id": self.alternate["print_item"].pk,
            },
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            404,
        )

    def test_11_print_item_foreign_company_is_404(self):
        url = reverse(
            "shipping_hub:print_item_asset",
            kwargs={
                "job_id": self.foreign["print_job"].pk,
                "item_id": self.foreign["print_item"].pk,
            },
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            404,
        )

    def test_12_print_item_wrong_same_company_warehouse_is_404(self):
        url = reverse(
            "shipping_hub:print_item_asset",
            kwargs={
                "job_id": self.wrong_warehouse["print_job"].pk,
                "item_id": self.wrong_warehouse["print_item"].pk,
            },
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            404,
        )

    # ================================================================
    # 13 CAPABILITY NEGATIVE
    # ================================================================

    def test_13_shipping_assets_without_shipping_view_are_403(self):
        self._login_with_warehouse(
            self.no_cap_user,
            self.warehouse_a1,
        )

        urls = (
            reverse(
                "shipping_hub:delivery_evidence_asset",
                kwargs={
                    "evidence_id": self.primary["evidence"].pk,
                    "asset_id": self.primary["asset"].pk,
                },
            ),
            reverse(
                "shipping_hub:external_package_label_file",
                kwargs={
                    "shipping_id": self.primary["shipping_order"].pk,
                    "package_id": self.primary["package"].pk,
                },
            ),
            reverse(
                "shipping_hub:print_item_asset",
                kwargs={
                    "job_id": self.primary["print_job"].pk,
                    "item_id": self.primary["print_item"].pk,
                },
            ),
        )

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(
                    response.status_code,
                    403,
                )