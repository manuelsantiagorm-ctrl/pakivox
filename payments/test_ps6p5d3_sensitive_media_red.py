from pathlib import Path

from django.test import SimpleTestCase

from payments.models import Payment
from purchases.models import SupplierPayment
from shipping_hub.models import (
    DeliveryEvidenceAsset,
    ShipmentPackage,
)


ROOT = Path(__file__).resolve().parent.parent


class PS6P5D3SensitiveMediaRedTests(SimpleTestCase):
    """
    Contrato de seguridad para archivos sensibles.

    Un permiso visual NO protege un FileField público.
    Los archivos financieros y logísticos sensibles deben:

    1. salir por una vista Django autorizada;
    2. respetar company + warehouse/object scope;
    3. no exponerse con .url desde la plantilla;
    4. almacenar nuevas cargas bajo prefijo privado.
    """

    @staticmethod
    def _text(relative_path):
        return (
            ROOT / relative_path
        ).read_text(
            encoding="utf-8",
        )

    def test_payment_proof_has_protected_backend_route(self):
        urls = self._text(
            "payments/urls.py"
        )

        self.assertIn(
            'name="ver_comprobante_pago"',
            urls,
        )

        self.assertIn(
            "payments.proof.view",
            urls,
        )

    def test_payment_template_never_exposes_filefield_url(self):
        template = self._text(
            "templates/payments/detalle_pago.html"
        )

        self.assertNotIn(
            "payment.proof_file.url",
            template,
        )

        self.assertIn(
            "payments:ver_comprobante_pago",
            template,
        )

    def test_payment_proof_backend_is_scoped_and_streamed(self):
        source = self._text(
            "payments/views.py"
        )

        self.assertIn(
            "def ver_comprobante_pago",
            source,
        )

        required_markers = (
            "get_request_read_warehouse_ids",
            "scope_payments_for_read",
            "get_object_or_404",
            'proof_file.open("rb")',
            "FileResponse",
        )

        for marker in required_markers:
            self.assertIn(
                marker,
                source,
            )

    def test_new_payment_proofs_use_private_storage_prefix(self):
        field = Payment._meta.get_field(
            "proof_file"
        )

        upload_to = str(
            field.upload_to
        )

        self.assertTrue(
            upload_to.startswith("private/"),
            upload_to,
        )

    def test_new_supplier_receipts_use_private_storage_prefix(self):
        field = SupplierPayment._meta.get_field(
            "receipt_file"
        )

        upload_to = str(
            field.upload_to
        )

        self.assertTrue(
            upload_to.startswith("private/"),
            upload_to,
        )

    def test_delivery_evidence_storage_is_already_private(self):
        field = DeliveryEvidenceAsset._meta.get_field(
            "file"
        )

        upload_to = str(
            field.upload_to
        )

        self.assertTrue(
            upload_to.startswith("private/"),
            upload_to,
        )

    def test_shipping_label_storage_is_already_private(self):
        field = ShipmentPackage._meta.get_field(
            "label_file"
        )

        upload_to = str(
            field.upload_to
        )

        self.assertTrue(
            upload_to.startswith("private/"),
            upload_to,
        )