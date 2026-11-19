from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class DuplicateGuidanceUxTests(SimpleTestCase):
    def setUp(self):
        path = (
            Path(settings.BASE_DIR)
            / "data_imports"
            / "templates"
            / "data_imports"
            / "duplicate_resolution.html"
        )
        self.source = path.read_text(encoding="utf-8")

    def test_attention_notice_explains_why_user_is_here(self):
        self.assertIn("Necesitamos tu atención", self.source)
        self.assertIn("posibles clientes duplicados", self.source)
        self.assertIn("evitar crear clientes repetidos", self.source)

    def test_bulk_resolution_is_explained_truthfully(self):
        self.assertIn("Resolución rápida", self.source)
        self.assertIn("Mantener existente, omitir", self.source)
        self.assertIn("alta confianza", self.source)
        self.assertIn("media confianza", self.source)

    def test_large_batches_recommend_bulk_resolution(self):
        self.assertIn("{% if pending_count > 30 %}", self.source)
        self.assertIn("No necesitas revisar los {{ pending_count }} uno por uno", self.source)

    def test_manual_resolution_remains_available(self):
        self.assertIn("Revisión manual", self.source)
        self.assertIn("Revisar uno por uno", self.source)

    def test_multi_company_safety_is_visible(self):
        self.assertIn("PAKIVOX solo compara registros dentro de", self.source)
        self.assertIn("{{ company.name }}", self.source)
