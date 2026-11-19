from io import BytesIO
import inspect
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase

from shipping_hub import views


class PS3B2ShippingAssetSecurityTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.company = SimpleNamespace(pk=41, id=41)
        self.user = SimpleNamespace(
            pk=77,
            id=77,
            is_authenticated=True,
        )
        self.warehouse = SimpleNamespace(pk=18, id=18)

    def _request(self, path="/shipping-assets/"):
        request = self.factory.get(path)
        request.user = self.user
        return request

    def _shipping_order(self):
        return SimpleNamespace(
            packing_order=SimpleNamespace(
                picking_order=SimpleNamespace(
                    warehouse=self.warehouse,
                )
            )
        )

    def _carrier(self, request_data=None):
        return SimpleNamespace(
            shipping_order=self._shipping_order(),
            request_data=request_data or {},
        )

    def _package(self, name=None, package_id=9):
        return SimpleNamespace(
            pk=package_id,
            package_number=2,
            label_format="pdf",
            label_file=(
                SimpleNamespace(name=name)
                if name is not None
                else None
            ),
            external_label_original_name="paquete 2.pdf",
            external_label_content_type="application/pdf",
            carrier_shipment=SimpleNamespace(
                shipping_order=self._shipping_order()
            ),
        )

    def test_master_authorizes_exact_object_warehouse(self):
        request = self._request()
        carrier = self._carrier({
            "external_label_storage_name": (
                "shipping_hub/company_41/"
                "external_labels/abc.pdf"
            ),
            "external_label_original_name": "guia.pdf",
        })

        with (
            patch.object(
                views,
                "_get_request_company_or_403",
                return_value=self.company,
            ),
            patch.object(
                views,
                "get_object_or_404",
                return_value=carrier,
            ) as lookup,
            patch(
                "accounts.activity_route_authorization."
                "authorize_activity_route"
            ) as authorize,
            patch.object(
                views.default_storage,
                "exists",
                return_value=True,
            ),
            patch.object(
                views.default_storage,
                "open",
                return_value=BytesIO(b"PDF"),
            ),
        ):
            response = views.shipping_external_label_download(
                request,
                shipping_id=123,
            )

        authorize.assert_called_once_with(
            request,
            "shipping_hub",
            "shipping_external_label_download",
            warehouse=self.warehouse,
        )
        self.assertEqual(
            lookup.call_args.kwargs["company"],
            self.company,
        )
        self.assertEqual(
            lookup.call_args.kwargs["shipping_order_id"],
            123,
        )
        self.assertEqual(response.status_code, 200)

    def test_master_denial_stops_before_storage(self):
        request = self._request()
        carrier = self._carrier({
            "external_label_storage_name": (
                "shipping_hub/company_41/"
                "external_labels/abc.pdf"
            )
        })

        with (
            patch.object(
                views,
                "_get_request_company_or_403",
                return_value=self.company,
            ),
            patch.object(
                views,
                "get_object_or_404",
                return_value=carrier,
            ),
            patch(
                "accounts.activity_route_authorization."
                "authorize_activity_route",
                side_effect=PermissionDenied("denied"),
            ),
            patch.object(
                views.default_storage,
                "exists",
            ) as exists,
        ):
            with self.assertRaises(PermissionDenied):
                views.shipping_external_label_download(
                    request,
                    shipping_id=123,
                )

        exists.assert_not_called()

    def test_master_without_registered_file_is_404(self):
        request = self._request()
        carrier = self._carrier({})

        with (
            patch.object(
                views,
                "_get_request_company_or_403",
                return_value=self.company,
            ),
            patch.object(
                views,
                "get_object_or_404",
                return_value=carrier,
            ),
            patch(
                "accounts.activity_route_authorization."
                "authorize_activity_route"
            ),
        ):
            with self.assertRaises(Http404):
                views.shipping_external_label_download(
                    request,
                    shipping_id=123,
                )

    def test_master_rejects_foreign_company_prefix(self):
        request = self._request()
        carrier = self._carrier({
            "external_label_storage_name": (
                "shipping_hub/company_999/"
                "external_labels/abc.pdf"
            )
        })

        with (
            patch.object(
                views,
                "_get_request_company_or_403",
                return_value=self.company,
            ),
            patch.object(
                views,
                "get_object_or_404",
                return_value=carrier,
            ),
            patch(
                "accounts.activity_route_authorization."
                "authorize_activity_route"
            ),
        ):
            with self.assertRaises(PermissionDenied):
                views.shipping_external_label_download(
                    request,
                    shipping_id=123,
                )

    def test_master_rejects_parent_traversal(self):
        request = self._request()
        carrier = self._carrier({
            "external_label_storage_name": (
                "shipping_hub/company_41/"
                "external_labels/../../secret.pdf"
            )
        })

        with (
            patch.object(
                views,
                "_get_request_company_or_403",
                return_value=self.company,
            ),
            patch.object(
                views,
                "get_object_or_404",
                return_value=carrier,
            ),
            patch(
                "accounts.activity_route_authorization."
                "authorize_activity_route"
            ),
        ):
            with self.assertRaises(PermissionDenied):
                views.shipping_external_label_download(
                    request,
                    shipping_id=123,
                )

    def test_master_missing_storage_object_is_404(self):
        request = self._request()
        carrier = self._carrier({
            "external_label_storage_name": (
                "shipping_hub/company_41/"
                "external_labels/abc.pdf"
            )
        })

        with (
            patch.object(
                views,
                "_get_request_company_or_403",
                return_value=self.company,
            ),
            patch.object(
                views,
                "get_object_or_404",
                return_value=carrier,
            ),
            patch(
                "accounts.activity_route_authorization."
                "authorize_activity_route"
            ),
            patch.object(
                views.default_storage,
                "exists",
                return_value=False,
            ),
        ):
            with self.assertRaises(Http404):
                views.shipping_external_label_download(
                    request,
                    shipping_id=123,
                )

    def test_master_returns_pdf_attachment(self):
        request = self._request()
        carrier = self._carrier({
            "external_label_storage_name": (
                "shipping_hub/company_41/"
                "external_labels/abc.pdf"
            ),
            "external_label_original_name": "Guia Cliente.pdf",
        })

        with (
            patch.object(
                views,
                "_get_request_company_or_403",
                return_value=self.company,
            ),
            patch.object(
                views,
                "get_object_or_404",
                return_value=carrier,
            ),
            patch(
                "accounts.activity_route_authorization."
                "authorize_activity_route"
            ),
            patch.object(
                views.default_storage,
                "exists",
                return_value=True,
            ),
            patch.object(
                views.default_storage,
                "open",
                return_value=BytesIO(b"PDF"),
            ),
        ):
            response = views.shipping_external_label_download(
                request,
                shipping_id=123,
            )

        self.assertEqual(
            response["Content-Type"],
            "application/pdf",
        )
        self.assertIn(
            "attachment",
            response["Content-Disposition"],
        )

    def test_package_authorizes_exact_object_warehouse(self):
        request = self._request()
        package = self._package(
            "shipping_hub/company_41/"
            "external_packages/package_9/abc.pdf"
        )
        inner = inspect.unwrap(views.external_package_label_file)

        with (
            patch.object(
                views,
                "_get_request_company_or_403",
                return_value=self.company,
            ),
            patch.object(
                views,
                "_external_package_or_404",
                return_value=package,
            ) as lookup,
            patch(
                "accounts.activity_route_authorization."
                "authorize_activity_route"
            ) as authorize,
            patch.object(
                views.default_storage,
                "exists",
                return_value=True,
            ),
            patch.object(
                views.default_storage,
                "open",
                return_value=BytesIO(b"PDF"),
            ),
        ):
            response = inner(
                request,
                shipping_id=123,
                package_id=9,
            )

        authorize.assert_called_once_with(
            request,
            "shipping_hub",
            "external_package_label_file",
            warehouse=self.warehouse,
        )
        lookup.assert_called_once_with(
            self.company,
            123,
            9,
        )
        self.assertEqual(response.status_code, 200)

    def test_package_rejects_wrong_package_prefix(self):
        request = self._request()
        package = self._package(
            "shipping_hub/company_41/"
            "external_packages/package_10/abc.pdf",
            package_id=9,
        )
        inner = inspect.unwrap(views.external_package_label_file)

        with (
            patch.object(
                views,
                "_get_request_company_or_403",
                return_value=self.company,
            ),
            patch.object(
                views,
                "_external_package_or_404",
                return_value=package,
            ),
            patch(
                "accounts.activity_route_authorization."
                "authorize_activity_route"
            ),
        ):
            with self.assertRaises(PermissionDenied):
                inner(
                    request,
                    shipping_id=123,
                    package_id=9,
                )

    def test_package_rejects_parent_traversal(self):
        request = self._request()
        package = self._package(
            "shipping_hub/company_41/"
            "external_packages/package_9/"
            "../package_10/secret.pdf"
        )
        inner = inspect.unwrap(views.external_package_label_file)

        with (
            patch.object(
                views,
                "_get_request_company_or_403",
                return_value=self.company,
            ),
            patch.object(
                views,
                "_external_package_or_404",
                return_value=package,
            ),
            patch(
                "accounts.activity_route_authorization."
                "authorize_activity_route"
            ),
        ):
            with self.assertRaises(PermissionDenied):
                inner(
                    request,
                    shipping_id=123,
                    package_id=9,
                )

    def test_package_missing_file_field_is_404(self):
        request = self._request()
        package = self._package(None)
        inner = inspect.unwrap(views.external_package_label_file)

        with (
            patch.object(
                views,
                "_get_request_company_or_403",
                return_value=self.company,
            ),
            patch.object(
                views,
                "_external_package_or_404",
                return_value=package,
            ),
            patch(
                "accounts.activity_route_authorization."
                "authorize_activity_route"
            ),
        ):
            with self.assertRaises(Http404):
                inner(
                    request,
                    shipping_id=123,
                    package_id=9,
                )

    def test_package_print_mode_is_inline(self):
        request = self.factory.get(
            "/shipping-assets/?print=1"
        )
        request.user = self.user
        package = self._package(
            "shipping_hub/company_41/"
            "external_packages/package_9/abc.pdf"
        )
        inner = inspect.unwrap(views.external_package_label_file)

        with (
            patch.object(
                views,
                "_get_request_company_or_403",
                return_value=self.company,
            ),
            patch.object(
                views,
                "_external_package_or_404",
                return_value=package,
            ),
            patch(
                "accounts.activity_route_authorization."
                "authorize_activity_route"
            ),
            patch.object(
                views.default_storage,
                "exists",
                return_value=True,
            ),
            patch.object(
                views.default_storage,
                "open",
                return_value=BytesIO(b"PDF"),
            ),
        ):
            response = inner(
                request,
                shipping_id=123,
                package_id=9,
            )

        self.assertIn(
            "inline",
            response["Content-Disposition"],
        )
