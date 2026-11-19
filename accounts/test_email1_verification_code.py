from unittest.mock import patch

from django.test import TestCase

from accounts.models import PendingCompanyRegistration
from accounts.services import (
    InvalidRegistrationToken,
    create_pending_registration,
    verify_registration_and_create_tenant,
)


class Email1VerificationCodeTests(TestCase):
    def _pending(self, email="owner@example.com"):
        return create_pending_registration(
            owner_name="Owner Test",
            email=email,
            company_name="Empresa Test",
            raw_password="S3gura!Prueba2026",
            accepted_legal_documents=[],
        )

    def _wrong_code(self, good_code):
        return "000000" if good_code != "000000" else "999999"

    def test_code_is_not_stored_plaintext(self):
        pending, code = self._pending()
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())
        self.assertNotEqual(pending.token_hash, code)
        self.assertTrue(pending.code_matches(code))

    def test_wrong_code_persists_attempt_after_controlled_exception(self):
        pending, code = self._pending()

        with self.assertRaises(InvalidRegistrationToken):
            verify_registration_and_create_tenant(
                pending_registration_id=pending.pk,
                raw_code=self._wrong_code(code),
            )

        pending.refresh_from_db()
        self.assertEqual(pending.verification_attempt_count, 1)
        self.assertEqual(
            pending.status,
            PendingCompanyRegistration.STATUS_PENDING,
        )

    def test_five_wrong_codes_cancel_registration(self):
        pending, code = self._pending()
        wrong = self._wrong_code(code)

        for _ in range(5):
            with self.assertRaises(InvalidRegistrationToken):
                verify_registration_and_create_tenant(
                    pending_registration_id=pending.pk,
                    raw_code=wrong,
                )

        pending.refresh_from_db()
        self.assertEqual(pending.verification_attempt_count, 5)
        self.assertEqual(
            pending.status,
            PendingCompanyRegistration.STATUS_CANCELLED,
        )

    def test_invalid_format_also_consumes_attempt(self):
        pending, _ = self._pending()

        with self.assertRaises(InvalidRegistrationToken):
            verify_registration_and_create_tenant(
                pending_registration_id=pending.pk,
                raw_code="ABC",
            )

        pending.refresh_from_db()
        self.assertEqual(pending.verification_attempt_count, 1)

    def test_pending_email_unique_at_database_level(self):
        self._pending()
        with self.assertRaises(Exception):
            self._pending()

    @patch("accounts.tasks.send_mail", return_value=1)
    def test_email_task_does_not_require_request(self, mocked):
        from accounts.tasks import send_registration_verification_email_task

        pending, code = self._pending()
        result = send_registration_verification_email_task.run(
            pending.pk,
            code,
            "",
        )

        self.assertEqual(result, 1)
        mocked.assert_called_once()


class Email1DeliverySafetyTests(TestCase):
    def _pending(self, email="delivery@example.com"):
        return create_pending_registration(
            owner_name="Delivery Test",
            email=email,
            company_name="Empresa Delivery",
            raw_password="S3gura!Prueba2026",
            accepted_legal_documents=[],
        )

    @patch("accounts.tasks.send_mail", return_value=1)
    def test_stale_code_is_not_sent(self, mocked):
        from accounts.tasks import send_registration_verification_email_task

        pending, old_code = self._pending()
        new_code = pending.generate_verification_code(minutes=10)
        pending.save(
            update_fields=[
                "token_hash",
                "expires_at",
                "verification_attempt_count",
                "status",
                "updated_at",
            ]
        )

        result = send_registration_verification_email_task.run(
            pending.pk,
            old_code,
            "",
        )

        self.assertEqual(result, "skipped:stale-code")
        mocked.assert_not_called()
        self.assertTrue(pending.code_matches(new_code))

    @patch("accounts.tasks.send_mail", return_value=1)
    def test_email_has_html_and_plaintext_fallback(self, mocked):
        from accounts.tasks import send_registration_verification_email_task

        pending, code = self._pending("html@example.com")
        result = send_registration_verification_email_task.run(
            pending.pk,
            code,
            "",
        )

        self.assertEqual(result, 1)
        kwargs = mocked.call_args.kwargs
        self.assertIn(code, kwargs["message"])
        self.assertIn(code, kwargs["html_message"])
        self.assertIn("PAKIVOX", kwargs["html_message"])
