from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class DuplicateAttentionJobDetailUxTests(SimpleTestCase):
    def setUp(self):
        path = (
            Path(settings.BASE_DIR)
            / "data_imports"
            / "templates"
            / "data_imports"
            / "job_detail.html"
        )
        self.source = path.read_text(encoding="utf-8")

    def test_job_detail_explains_why_duplicates_block_progress(self):
        self.assertIn(
            "Necesitamos tu atención antes de continuar",
            self.source,
        )
        self.assertIn(
            "evitar crear clientes repetidos",
            self.source,
        )

    def test_large_duplicate_batches_explain_bulk_option_before_navigation(self):
        self.assertIn(
            "{% if duplicate_pending_count > 30 %}",
            self.source,
        )
        self.assertIn(
            "No necesitas revisar los {{ duplicate_pending_count }} uno por uno",
            self.source,
        )
        self.assertIn(
            "Resolución rápida",
            self.source,
        )

    def test_manual_review_is_explained_on_job_detail(self):
        self.assertIn(
            "Revisión manual",
            self.source,
        )
        self.assertIn(
            "caso por caso",
            self.source,
        )

    def test_primary_cta_describes_next_step(self):
        self.assertIn(
            "Ver opciones para resolver",
            self.source,
        )

    def test_resolved_state_is_not_shown_as_warning(self):
        self.assertIn(
            "Duplicados resueltos",
            self.source,
        )
        self.assertIn(
            "No quedan duplicados pendientes en este lote",
            self.source,
        )
