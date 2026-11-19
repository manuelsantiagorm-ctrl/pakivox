from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from messaging.meta_api import (
    MetaWhatsAppConfigurationError,
    normalize_meta_phone,
    send_meta_whatsapp_text,
)


class MetaPhoneNormalizationTests(SimpleTestCase):
    def test_mexico_standard_52_is_preserved(self):
        self.assertEqual(
            normalize_meta_phone("+52 56 5008 2857"),
            "525550000000",
        )

    def test_mexico_legacy_521_is_converted_to_52(self):
        self.assertEqual(
            normalize_meta_phone("5215550000000"),
            "525550000000",
        )

    def test_mexico_normalization_is_idempotent(self):
        first_result = normalize_meta_phone(
            "5215550000000"
        )

        self.assertEqual(
            normalize_meta_phone(first_result),
            first_result,
        )

    def test_other_country_is_not_modified(self):
        self.assertEqual(
            normalize_meta_phone("+1 (555) 669-6900"),
            "15556696900",
        )

    def test_inbound_from_and_contact_wa_id_match(self):
        self.assertEqual(
            normalize_meta_phone("5215550000000"),
            normalize_meta_phone("525550000000"),
        )

    def test_invalid_short_phone_is_rejected(self):
        with self.assertRaises(
            MetaWhatsAppConfigurationError
        ):
            normalize_meta_phone("123")

    def test_outbound_payload_uses_mexico_52_format(self):
        response = Mock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {
            "messaging_product": "whatsapp",
            "messages": [
                {
                    "id": "wamid.TEST_META_521",
                }
            ],
        }

        credentials = {
            "phone_number_id": "1284214654769161",
            "access_token": "TOKEN_SOLO_PARA_TEST",
            "api_version": "v25.0",
        }

        with patch(
            "messaging.meta_api."
            "get_meta_credentials_from_channel",
            return_value=credentials,
        ), patch(
            "messaging.meta_api.requests.post",
            return_value=response,
        ) as post_mock:
            result = send_meta_whatsapp_text(
                whatsapp_number=object(),
                to_phone="5215550000000",
                body="Prueba normalización México",
            )

        post_mock.assert_called_once()

        request_kwargs = (
            post_mock.call_args.kwargs
        )

        self.assertEqual(
            request_kwargs["json"]["to"],
            "525550000000",
        )


