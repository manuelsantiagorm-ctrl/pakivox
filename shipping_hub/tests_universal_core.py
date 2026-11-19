from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase

from accounts.warehouse_access import ACCESS_OPERATE
from shipping_hub.forms import ExternalShippingLabelForm
from shipping_hub.models import CarrierShipment
from shipping_hub.services.cubication import prepare_cubication
from shipping_hub.views import _authorize_shipping_warehouse


class UniversalShippingCoreTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get("/shipping-hub/")
        self.request.user = Mock(is_authenticated=True)
        self.company = SimpleNamespace(id=7)
        self.shipping_order = SimpleNamespace(company_id=7)
        self.warehouse = SimpleNamespace(pk=3, company_id=7)
        self.context = SimpleNamespace(
            warehouse=self.warehouse,
            shipping_order=self.shipping_order,
        )

    def test_universal_sources_are_available(self):
        values = {value for value, _label in CarrierShipment.SOURCE_CHOICES}
        self.assertTrue({"automatic", "external", "local", "custom", "direct"} <= values)

    def test_prepare_cubication_defaults_to_no_integration(self):
        self.assertFalse(prepare_cubication.__kwdefaults__["require_integration"])

    @patch("shipping_hub.views.user_can_access_warehouse", return_value=True)
    @patch("shipping_hub.views.require_active_warehouse")
    def test_real_shipping_warehouse_is_authorized(self, active, _can_access):
        active.return_value = self.warehouse
        result = _authorize_shipping_warehouse(
            self.request, self.context, required_level=ACCESS_OPERATE
        )
        self.assertIs(result, self.warehouse)

    @patch("shipping_hub.views.user_can_access_warehouse", return_value=True)
    @patch("shipping_hub.views.require_active_warehouse")
    def test_selected_foreign_warehouse_is_rejected(self, active, _can_access):
        active.return_value = SimpleNamespace(pk=99, company_id=7)
        with self.assertRaises(PermissionDenied):
            _authorize_shipping_warehouse(
                self.request, self.context, required_level=ACCESS_OPERATE
            )

    @patch("shipping_hub.views.require_active_warehouse")
    def test_insufficient_warehouse_level_is_rejected(self, active):
        active.side_effect = PermissionDenied("nivel insuficiente")
        with self.assertRaises(PermissionDenied):
            _authorize_shipping_warehouse(
                self.request, self.context, required_level=ACCESS_OPERATE
            )

    def test_external_label_rejects_fake_pdf(self):
        upload = SimpleUploadedFile(
            "label.pdf", b"not-a-pdf", content_type="text/plain"
        )
        form = ExternalShippingLabelForm(
            data={
                "carrier_name": "DHL",
                "tracking_number": "ABC1234",
                "currency": "MXN",
            },
            files={"label_file": upload},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("label_file", form.errors)
