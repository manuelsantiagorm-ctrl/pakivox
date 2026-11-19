from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import (
    Capability,
    EmployeeCapabilityAssignment,
    UserProfile,
    UserWarehouseAccess,
)
from companies.models import Company
from crm.models import Customer
from inventory.models import Warehouse
from payments.models import Payment
from sales.models import Order
from warehouse.models import OrderFulfillment


User = get_user_model()


class PS6P5D3PaymentProofRuntimeTests(TestCase):
    """
    PS-6P.5D3B2

    Prueba runtime real de aislamiento del comprobante de Payment:

    - capability
    - company
    - warehouse
    - parent Order scope
    - compatibilidad histórica
    - streaming únicamente por backend autorizado
    """

    def setUp(self):
        self.media_dir = TemporaryDirectory()

        self.media_override = override_settings(
            MEDIA_ROOT=self.media_dir.name,
        )
        self.media_override.enable()

        # ------------------------------------------------------
        # EMPRESAS
        # ------------------------------------------------------

        self.company = Company.objects.create(
            name="D3 Runtime Empresa A",
            slug="d3-runtime-empresa-a",
        )

        self.foreign_company = Company.objects.create(
            name="D3 Runtime Empresa B",
            slug="d3-runtime-empresa-b",
        )

        # ------------------------------------------------------
        # USUARIO ACTUAL
        # ------------------------------------------------------

        self.user = User.objects.create_user(
            username="d3-runtime@test.local",
            password="D3-Runtime-StrongPass-123!",
        )

        UserProfile.objects.create(
            user=self.user,
            company=self.company,
            role="sales",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )

        # ------------------------------------------------------
        # USUARIO TENANT AJENO
        # ------------------------------------------------------

        self.foreign_user = User.objects.create_user(
            username="d3-runtime-foreign@test.local",
            password="D3-Foreign-StrongPass-123!",
        )

        UserProfile.objects.create(
            user=self.foreign_user,
            company=self.foreign_company,
            role="owner",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )

        # ------------------------------------------------------
        # BODEGAS
        # ------------------------------------------------------

        self.warehouse_a = Warehouse.objects.create(
            company=self.company,
            name="D3 Bodega A",
            code="D3-RUNTIME-A",
        )

        self.warehouse_b = Warehouse.objects.create(
            company=self.company,
            name="D3 Bodega B",
            code="D3-RUNTIME-B",
        )

        self.foreign_warehouse = Warehouse.objects.create(
            company=self.foreign_company,
            name="D3 Bodega X",
            code="D3-RUNTIME-X",
        )

        # El usuario solamente puede leer Bodega A.
        UserWarehouseAccess.objects.create(
            company=self.company,
            user=self.user,
            warehouse=self.warehouse_a,
            access_level=UserWarehouseAccess.LEVEL_VIEW,
            is_active=True,
        )

        # ------------------------------------------------------
        # CLIENTES
        # ------------------------------------------------------

        self.customer = Customer.objects.create(
            company=self.company,
            full_name="Cliente D3 Runtime",
        )

        self.foreign_customer = Customer.objects.create(
            company=self.foreign_company,
            full_name="Cliente Ajeno D3 Runtime",
        )

        # ------------------------------------------------------
        # PEDIDOS
        # ------------------------------------------------------

        self.order_a = self._create_order(
            company=self.company,
            customer=self.customer,
            created_by=self.user,
            folio="D3-RUNTIME-ORDER-A",
        )

        self.order_b = self._create_order(
            company=self.company,
            customer=self.customer,
            created_by=self.user,
            folio="D3-RUNTIME-ORDER-B",
        )

        self.foreign_order = self._create_order(
            company=self.foreign_company,
            customer=self.foreign_customer,
            created_by=self.foreign_user,
            folio="D3-RUNTIME-ORDER-X",
        )

        # ------------------------------------------------------
        # ALCANCE FÍSICO PERSISTIDO
        # ------------------------------------------------------

        OrderFulfillment.objects.create(
            company=self.company,
            order=self.order_a,
            warehouse=self.warehouse_a,
            sequence=1,
        )

        OrderFulfillment.objects.create(
            company=self.company,
            order=self.order_b,
            warehouse=self.warehouse_b,
            sequence=1,
        )

        OrderFulfillment.objects.create(
            company=self.foreign_company,
            order=self.foreign_order,
            warehouse=self.foreign_warehouse,
            sequence=1,
        )

        # ------------------------------------------------------
        # PAGOS + ARCHIVOS REALES
        # ------------------------------------------------------

        self.payment_a = self._create_payment(
            company=self.company,
            order=self.order_a,
            registered_by=self.user,
            folio="D3-RUNTIME-PAY-A",
            filename="proof-a.txt",
            content=b"proof-company-a-warehouse-a",
        )

        self.payment_b = self._create_payment(
            company=self.company,
            order=self.order_b,
            registered_by=self.user,
            folio="D3-RUNTIME-PAY-B",
            filename="proof-b.txt",
            content=b"proof-company-a-warehouse-b",
        )

        self.foreign_payment = self._create_payment(
            company=self.foreign_company,
            order=self.foreign_order,
            registered_by=self.foreign_user,
            folio="D3-RUNTIME-PAY-X",
            filename="proof-x.txt",
            content=b"proof-foreign-company",
        )

        self.client.force_login(
            self.user
        )

    def tearDown(self):
        self.media_override.disable()
        self.media_dir.cleanup()

    @staticmethod
    def _create_order(
        *,
        company,
        customer,
        created_by,
        folio,
    ):
        return Order.objects.create(
            company=company,
            customer=customer,
            created_by=created_by,
            folio=folio,
            status="draft",
        )

    @staticmethod
    def _create_payment(
        *,
        company,
        order,
        registered_by,
        folio,
        filename,
        content,
    ):
        return Payment.objects.create(
            company=company,
            order=order,
            registered_by=registered_by,
            folio=folio,
            payment_method="manual",
            payment_type="deposit",
            status="pending",
            amount="10.00",
            proof_file=SimpleUploadedFile(
                filename,
                content,
                content_type="text/plain",
            ),
        )

    def _grant_proof_capability(self):
        capability, _ = Capability.objects.update_or_create(
            code="payments.proof.view",
            defaults={
                "name": "Ver comprobantes de pago",
                "description": (
                    "Permite consultar comprobantes de pagos autorizados."
                ),
                "area_code": "collections_payments",
                "is_active": True,
            },
        )

        EmployeeCapabilityAssignment.objects.update_or_create(
            company=self.company,
            user=self.user,
            capability=capability,
            defaults={
                "is_active": True,
            },
        )

    @staticmethod
    def _stream_bytes(response):
        return b"".join(
            response.streaming_content
        )

    @staticmethod
    def _proof_url(payment):
        return reverse(
            "payments:ver_comprobante_pago",
            kwargs={
                "payment_id": payment.pk,
            },
        )

    # ==========================================================
    # 1. CAPABILITY
    # ==========================================================

    def test_01_without_proof_capability_returns_403(self):
        response = self.client.get(
            self._proof_url(self.payment_a)
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    # ==========================================================
    # 2. HAPPY PATH
    # ==========================================================

    def test_02_authorized_company_and_warehouse_returns_200(self):
        self._grant_proof_capability()

        response = self.client.get(
            self._proof_url(self.payment_a)
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            self._stream_bytes(response),
            b"proof-company-a-warehouse-a",
        )

        self.assertTrue(
            self.payment_a.proof_file.name.startswith(
                "private/payment_proofs/"
            ),
            self.payment_a.proof_file.name,
        )

        self.assertEqual(
            response["Cache-Control"],
            "private, no-store",
        )

        self.assertEqual(
            response["X-Content-Type-Options"],
            "nosniff",
        )

    # ==========================================================
    # 3. SAME COMPANY / WRONG WAREHOUSE
    # ==========================================================

    def test_03_same_company_other_warehouse_returns_404(self):
        self._grant_proof_capability()

        response = self.client.get(
            self._proof_url(self.payment_b)
        )

        self.assertEqual(
            response.status_code,
            404,
        )

    # ==========================================================
    # 4. FOREIGN COMPANY
    # ==========================================================

    def test_04_foreign_company_payment_returns_404(self):
        self._grant_proof_capability()

        response = self.client.get(
            self._proof_url(self.foreign_payment)
        )

        self.assertEqual(
            response.status_code,
            404,
        )

    # ==========================================================
    # 5. LEGACY FILE
    # ==========================================================

    def test_05_legacy_payment_proof_is_readable_only_via_backend(self):
        self._grant_proof_capability()

        legacy_relative = Path(
            "payment_proofs",
            "legacy-d3-runtime.txt",
        )

        legacy_absolute = (
            Path(self.media_dir.name)
            / legacy_relative
        )

        legacy_absolute.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        legacy_absolute.write_bytes(
            b"legacy-payment-proof"
        )

        payment = Payment.objects.create(
            company=self.company,
            order=self.order_a,
            registered_by=self.user,
            folio="D3-RUNTIME-PAY-LEGACY",
            payment_method="manual",
            payment_type="deposit",
            status="pending",
            amount="10.00",
        )

        payment.proof_file.name = (
            legacy_relative.as_posix()
        )

        payment.save(
            update_fields=[
                "proof_file",
                "updated_at",
            ]
        )

        response = self.client.get(
            self._proof_url(payment)
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            self._stream_bytes(response),
            b"legacy-payment-proof",
        )

        self.assertEqual(
            payment.proof_file.name,
            "payment_proofs/legacy-d3-runtime.txt",
        )