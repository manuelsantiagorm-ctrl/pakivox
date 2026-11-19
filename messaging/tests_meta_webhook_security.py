import hashlib
import hmac
import json

from django.test import (
    SimpleTestCase,
    override_settings,
)

from messaging.meta_webhook import (
    MetaWebhookConfigurationError,
    MetaWebhookPayloadError,
    MetaWebhookSignatureError,
    MetaWebhookVerificationError,
    extract_meta_message_changes,
    parse_meta_webhook_payload,
    verify_meta_webhook_challenge,
    verify_meta_webhook_signature,
)


class MetaWebhookSecurityTests(SimpleTestCase):
    """
    No utiliza DB.
    No realiza llamadas externas.
    No requiere secretos reales.
    """

    # ========================================================
    # GET
    # ========================================================

    @override_settings(
        META_WEBHOOK_VERIFY_TOKEN="VERIFY_TEST_123",
    )
    def test_valid_challenge_is_accepted(self):
        result = verify_meta_webhook_challenge(
            mode="subscribe",
            verify_token="VERIFY_TEST_123",
            challenge="987654321",
        )

        self.assertEqual(
            result,
            "987654321",
        )

    @override_settings(
        META_WEBHOOK_VERIFY_TOKEN="VERIFY_TEST_123",
    )
    def test_invalid_verify_token_is_rejected(self):
        with self.assertRaises(
            MetaWebhookVerificationError
        ):
            verify_meta_webhook_challenge(
                mode="subscribe",
                verify_token="WRONG",
                challenge="123",
            )

    @override_settings(
        META_WEBHOOK_VERIFY_TOKEN="VERIFY_TEST_123",
    )
    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(
            MetaWebhookVerificationError
        ):
            verify_meta_webhook_challenge(
                mode="invalid",
                verify_token="VERIFY_TEST_123",
                challenge="123",
            )

    @override_settings(
        META_WEBHOOK_VERIFY_TOKEN="",
    )
    def test_missing_server_verify_token_is_rejected(self):
        with self.assertRaises(
            MetaWebhookConfigurationError
        ):
            verify_meta_webhook_challenge(
                mode="subscribe",
                verify_token="ANY",
                challenge="123",
            )

    # ========================================================
    # POST SIGNATURE
    # ========================================================

    @override_settings(
        META_APP_SECRET="APP_SECRET_TEST_123",
    )
    def test_valid_signature_is_accepted(self):
        raw_body = b'{"hello":"world"}'

        digest = hmac.new(
            b"APP_SECRET_TEST_123",
            raw_body,
            hashlib.sha256,
        ).hexdigest()

        result = verify_meta_webhook_signature(
            raw_body=raw_body,
            signature_header=(
                f"sha256={digest}"
            ),
        )

        self.assertTrue(result)

    @override_settings(
        META_APP_SECRET="APP_SECRET_TEST_123",
    )
    def test_invalid_signature_is_rejected(self):
        with self.assertRaises(
            MetaWebhookSignatureError
        ):
            verify_meta_webhook_signature(
                raw_body=b'{"hello":"world"}',
                signature_header=(
                    "sha256="
                    + ("0" * 64)
                ),
            )

    @override_settings(
        META_APP_SECRET="",
    )
    def test_missing_app_secret_blocks_post(self):
        with self.assertRaises(
            MetaWebhookConfigurationError
        ):
            verify_meta_webhook_signature(
                raw_body=b"{}",
                signature_header=(
                    "sha256="
                    + ("0" * 64)
                ),
            )

    # ========================================================
    # JSON
    # ========================================================

    def test_valid_payload_is_parsed(self):
        payload = {
            "object": (
                "whatsapp_business_account"
            ),
            "entry": [],
        }

        raw_body = json.dumps(
            payload
        ).encode("utf-8")

        result = parse_meta_webhook_payload(
            raw_body
        )

        self.assertEqual(
            result,
            payload,
        )

    def test_invalid_json_is_rejected(self):
        with self.assertRaises(
            MetaWebhookPayloadError
        ):
            parse_meta_webhook_payload(
                b"{invalid"
            )

    def test_wrong_object_is_rejected(self):
        raw_body = json.dumps(
            {
                "object": "page",
                "entry": [],
            }
        ).encode("utf-8")

        with self.assertRaises(
            MetaWebhookPayloadError
        ):
            parse_meta_webhook_payload(
                raw_body
            )

    # ========================================================
    # PHONE NUMBER ID / MESSAGES / STATUSES
    # ========================================================

    def test_message_change_is_extracted(self):
        payload = {
            "object": (
                "whatsapp_business_account"
            ),
            "entry": [
                {
                    "id": "WABA123",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": (
                                    "whatsapp"
                                ),
                                "metadata": {
                                    "display_phone_number": (
                                        "15551234567"
                                    ),
                                    "phone_number_id": (
                                        "123456789012345"
                                    ),
                                },
                                "messages": [
                                    {
                                        "id": (
                                            "wamid.IN001"
                                        ),
                                        "from": (
                                            "525512345678"
                                        ),
                                        "type": "text",
                                    }
                                ],
                                "statuses": [],
                            },
                        }
                    ],
                }
            ],
        }

        changes = extract_meta_message_changes(
            payload
        )

        self.assertEqual(
            len(changes),
            1,
        )

        self.assertEqual(
            changes[0]["waba_id"],
            "WABA123",
        )

        self.assertEqual(
            changes[0]["phone_number_id"],
            "123456789012345",
        )

        self.assertEqual(
            changes[0]["messages"][0]["id"],
            "wamid.IN001",
        )

    def test_missing_phone_number_id_is_rejected(self):
        payload = {
            "object": (
                "whatsapp_business_account"
            ),
            "entry": [
                {
                    "id": "WABA123",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {},
                            },
                        }
                    ],
                }
            ],
        }

        with self.assertRaises(
            MetaWebhookPayloadError
        ):
            extract_meta_message_changes(
                payload
            )
