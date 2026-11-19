from django.test import SimpleTestCase

from shipping_hub.services.secret_manager import (
    generate_secret,
    masked_hint,
    secret_fingerprint,
)


class SecretManagerPrimitiveTests(SimpleTestCase):
    def test_generated_secret_has_sufficient_entropy_length(self):
        self.assertGreaterEqual(len(generate_secret()), 40)

    def test_generated_secrets_are_different(self):
        self.assertNotEqual(generate_secret(), generate_secret())

    def test_fingerprint_is_stable_and_not_plaintext(self):
        value = "demo-secret-value"
        fingerprint = secret_fingerprint(value)
        self.assertEqual(fingerprint, secret_fingerprint(value))
        self.assertNotIn(value, fingerprint)
        self.assertEqual(len(fingerprint), 64)

    def test_mask_only_exposes_last_four(self):
        self.assertEqual(masked_hint("abcdefgh"), "****efgh")

    def test_short_values_are_fully_masked(self):
        self.assertEqual(masked_hint("abc"), "****")
