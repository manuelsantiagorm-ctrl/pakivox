from django.test import TestCase
from django.urls import reverse

from accounts.services import create_pending_registration


class Email1ProfessionalUiTests(TestCase):
    def test_registration_page_renders(self):
        response = self.client.get(reverse("accounts:company_register"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PAKIVOX")

    def test_otp_page_has_six_visual_inputs_and_secure_form(self):
        pending, _ = create_pending_registration(
            owner_name="UI Test",
            email="ui@example.com",
            company_name="UI Empresa",
            raw_password="S3gura!Prueba2026",
            accepted_legal_documents=[],
        )
        session = self.client.session
        session["pending_registration_id"] = pending.pk
        session["pending_registration_email"] = pending.email
        session.save()

        response = self.client.get(reverse("accounts:registration_email_sent"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="otp"', count=6)
        self.assertContains(response, 'name="code"')
        self.assertContains(response, "Verificar y activar PAKIVOX")


class Email1MaskEmailTests(TestCase):
    def test_mask_email_does_not_expose_full_local_part(self):
        from accounts.views import mask_email

        self.assertEqual(mask_email("manuel@example.com"), "m****l@example.com")
        self.assertEqual(mask_email("ab@example.com"), "a*@example.com")
        self.assertEqual(mask_email("a@example.com"), "*@example.com")
