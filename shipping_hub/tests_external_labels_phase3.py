from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from shipping_hub.forms import PackageExternalLabelForm
from shipping_hub.models import CustomCarrier, ShipmentPackage


BASE = {"carrier_name": "Fletera del Bajío", "tracking_number": "EXT-12345"}


class ExternalLabelsPhase3Tests(SimpleTestCase):
    def make_form(self, name, content, content_type="application/octet-stream"):
        return PackageExternalLabelForm(
            data=BASE,
            files={"label_file": SimpleUploadedFile(name, content, content_type=content_type)},
            company=None,
        )

    def test_pdf_magic_bytes_are_accepted(self):
        form = self.make_form("guia.pdf", b"%PDF-1.7\ncontenido", "application/pdf")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["label_file"].wms_label_format, "pdf")

    def test_fake_pdf_is_rejected(self):
        form = self.make_form("guia.pdf", b"archivo falso", "application/pdf")
        self.assertFalse(form.is_valid())
        self.assertIn("label_file", form.errors)

    def test_png_signature_is_accepted(self):
        form = self.make_form("guia.png", b"\x89PNG\r\n\x1a\nresto", "image/png")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["label_file"].wms_label_format, "png")

    def test_fake_png_is_rejected(self):
        self.assertFalse(self.make_form("guia.png", b"not png", "image/png").is_valid())

    def test_valid_zpl_is_accepted(self):
        form = self.make_form("guia.zpl", b"^XA\n^FO20,20^FDWMS360^FS\n^XZ", "text/plain")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["label_file"].wms_label_format, "zpl")

    def test_invalid_zpl_is_rejected(self):
        self.assertFalse(self.make_form("guia.zpl", b"texto cualquiera", "text/plain").is_valid())

    def test_package_formats_include_pdf_png_zpl(self):
        values = {value for value, _ in ShipmentPackage.LABEL_FORMAT_CHOICES}
        self.assertEqual(values, {"pdf", "png", "zpl"})

    def test_custom_carrier_is_company_scoped(self):
        self.assertIn("company", {field.name for field in CustomCarrier._meta.fields})
