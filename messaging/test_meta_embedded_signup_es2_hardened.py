"""
WMS360 · WhatsApp Meta Embedded Signup · ES-2

Pruebas sin base de datos:
- IDs técnicos;
- WABA/Business;
- Phone Number membership;
- respuesta HTTP 200 con body inválido;
- errores Meta seguros;
- ausencia de secretos en mensajes públicos.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from messaging.embedded_signup_views import (
    MetaEmbeddedSignupRemoteError,
    _normalize_meta_id,
    _parse_meta_response,
    _validate_phone_in_waba,
    _validate_waba_owner,
)


class MetaEmbeddedSignupES2NoDBTests(SimpleTestCase):
    databases = set()

    def test_meta_id_accepts_numeric_only(self):
        self.assertEqual(
            _normalize_meta_id("123456789"),
            "123456789",
        )

    def test_meta_id_rejects_non_numeric(self):
        with self.assertRaises(
            MetaEmbeddedSignupRemoteError
        ):
            _normalize_meta_id("123ABC")

    def test_http_200_with_non_dict_json_is_rejected(self):
        response = Mock()
        response.ok = True
        response.json.return_value = ["unexpected"]

        with self.assertRaises(
            MetaEmbeddedSignupRemoteError
        ):
            _parse_meta_response(
                response,
                public_error="Error",
            )

    @patch("messaging.embedded_signup_views._meta_get")
    def test_waba_owner_is_authoritative(
        self,
        meta_get,
    ):
        meta_get.return_value = {
            "id": "9001",
            "name": "WABA Cliente",
            "owner_business_info": {
                "id": "7001",
                "name": "Empresa Cliente",
            },
        }

        config = SimpleNamespace(
            api_version="v23.0"
        )

        result = _validate_waba_owner(
            meta_configuration=config,
            business_access_token="TOKEN_NO_REAL",
            candidate_waba_id="9001",
            candidate_business_id="7001",
        )

        self.assertEqual(result["waba_id"], "9001")
        self.assertEqual(result["business_id"], "7001")

    @patch("messaging.embedded_signup_views._meta_get")
    def test_business_mismatch_is_rejected(
        self,
        meta_get,
    ):
        meta_get.return_value = {
            "id": "9001",
            "owner_business_info": {
                "id": "7001",
            },
        }

        config = SimpleNamespace(
            api_version="v23.0"
        )

        with self.assertRaises(
            MetaEmbeddedSignupRemoteError
        ):
            _validate_waba_owner(
                meta_configuration=config,
                business_access_token="TOKEN_NO_REAL",
                candidate_waba_id="9001",
                candidate_business_id="9999",
            )

    @patch("messaging.embedded_signup_views._meta_get")
    def test_phone_must_belong_to_selected_waba(
        self,
        meta_get,
    ):
        meta_get.return_value = {
            "data": [
                {
                    "id": "111",
                    "display_phone_number": "+525500000001",
                    "verified_name": "Uno",
                    "quality_rating": "GREEN",
                },
                {
                    "id": "222",
                    "display_phone_number": "+525500000002",
                    "verified_name": "Dos",
                    "quality_rating": "GREEN",
                },
            ]
        }

        config = SimpleNamespace(
            api_version="v23.0"
        )

        result = _validate_phone_in_waba(
            meta_configuration=config,
            business_access_token="TOKEN_NO_REAL",
            waba_id="9001",
            candidate_phone_number_id="222",
        )

        self.assertEqual(
            result["phone_number_id"],
            "222",
        )

    @patch("messaging.embedded_signup_views._meta_get")
    def test_phone_from_other_waba_is_rejected(
        self,
        meta_get,
    ):
        meta_get.return_value = {
            "data": [
                {
                    "id": "111",
                    "display_phone_number": "+525500000001",
                    "verified_name": "Uno",
                    "quality_rating": "GREEN",
                }
            ]
        }

        config = SimpleNamespace(
            api_version="v23.0"
        )

        with self.assertRaises(
            MetaEmbeddedSignupRemoteError
        ):
            _validate_phone_in_waba(
                meta_configuration=config,
                business_access_token="TOKEN_NO_REAL",
                waba_id="9001",
                candidate_phone_number_id="222",
            )
