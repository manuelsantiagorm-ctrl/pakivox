from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from messaging.meta_provisioning import (
    MetaProvisioningError,
    _ensure_waba_subscription,
    _subscription_payload_has_app,
)


class MetaProvisioningES3NoDBTests(SimpleTestCase):
    databases = set()

    def test_subscription_parser_requires_matching_app(self):
        payload = {
            "data": [
                {
                    "whatsapp_business_api_data": {
                        "id": "111",
                        "name": "Otra App",
                    }
                },
                {
                    "whatsapp_business_api_data": {
                        "id": "222",
                        "name": "WMS360",
                    }
                },
            ]
        }

        self.assertTrue(
            _subscription_payload_has_app(
                payload,
                app_id="222",
            )
        )
        self.assertFalse(
            _subscription_payload_has_app(
                payload,
                app_id="999",
            )
        )

    @patch("messaging.meta_provisioning._is_waba_subscribed")
    @patch("messaging.meta_provisioning._meta_request")
    def test_already_subscribed_does_not_post(
        self,
        meta_request,
        is_subscribed,
    ):
        is_subscribed.return_value = True

        config = SimpleNamespace(
            api_version="v23.0",
            app_id="222",
        )

        _ensure_waba_subscription(
            meta_configuration=config,
            access_token="TOKEN_NO_REAL",
            waba_id="9001",
        )

        meta_request.assert_not_called()

    @patch("messaging.meta_provisioning._is_waba_subscribed")
    @patch("messaging.meta_provisioning._meta_request")
    def test_post_must_be_verified_after_success(
        self,
        meta_request,
        is_subscribed,
    ):
        is_subscribed.side_effect = [
            False,
            True,
        ]
        meta_request.return_value = {
            "success": True,
        }

        config = SimpleNamespace(
            api_version="v23.0",
            app_id="222",
        )

        _ensure_waba_subscription(
            meta_configuration=config,
            access_token="TOKEN_NO_REAL",
            waba_id="9001",
        )

        self.assertEqual(
            meta_request.call_count,
            1,
        )

    @patch("messaging.meta_provisioning._is_waba_subscribed")
    @patch("messaging.meta_provisioning._meta_request")
    def test_http_success_without_remote_visibility_fails(
        self,
        meta_request,
        is_subscribed,
    ):
        is_subscribed.side_effect = [
            False,
            False,
        ]
        meta_request.return_value = {
            "success": True,
        }

        config = SimpleNamespace(
            api_version="v23.0",
            app_id="222",
        )

        with self.assertRaises(
            MetaProvisioningError
        ):
            _ensure_waba_subscription(
                meta_configuration=config,
                access_token="TOKEN_NO_REAL",
                waba_id="9001",
            )

    @patch("messaging.meta_provisioning._is_waba_subscribed")
    @patch("messaging.meta_provisioning._meta_request")
    def test_post_body_must_confirm_success(
        self,
        meta_request,
        is_subscribed,
    ):
        is_subscribed.return_value = False
        meta_request.return_value = {
            "success": False,
        }

        config = SimpleNamespace(
            api_version="v23.0",
            app_id="222",
        )

        with self.assertRaises(
            MetaProvisioningError
        ):
            _ensure_waba_subscription(
                meta_configuration=config,
                access_token="TOKEN_NO_REAL",
                waba_id="9001",
            )
