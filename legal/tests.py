from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from companies.models import Company
from legal.documents import DOCUMENTS, current_acceptance_snapshot
from legal.models import LegalAcceptance


@override_settings(
    LEGAL_OPERATOR_NAME="Operador de Prueba",
    LEGAL_RFC="XAXX010101000",
    LEGAL_ADDRESS="Domicilio de prueba",
)
class LegalPublicTests(TestCase):
    def test_all_legal_documents_are_public(self):
        for slug, document in DOCUMENTS.items():
            response = self.client.get(reverse("legal:document", args=[slug]))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, document["title"])
            self.assertContains(response, document["version"])

    def test_unknown_document_is_404(self):
        response = self.client.get(reverse("legal:document", args=["no-existe"]))
        self.assertEqual(response.status_code, 404)

    def test_snapshot_has_stable_audit_fields(self):
        snapshot = current_acceptance_snapshot()
        self.assertEqual(len(snapshot), 3)
        for item in snapshot:
            self.assertEqual(set(item), {"slug", "version", "sha256"})
            self.assertEqual(len(item["sha256"]), 64)

    def test_acceptance_is_scoped_to_company(self):
        user = get_user_model().objects.create_user("legal@example.com")
        company = Company.objects.create(
            name="Legal Test",
            slug="legal-test",
            email="legal@example.com",
        )
        LegalAcceptance.objects.create(
            user=user,
            company=company,
            document_slug="terminos-condiciones",
            document_version="2026-08-12.1",
            document_sha256="a" * 64,
            accepted_at="2026-08-12T12:00:00Z",
        )
        self.assertEqual(company.legal_acceptances.count(), 1)
