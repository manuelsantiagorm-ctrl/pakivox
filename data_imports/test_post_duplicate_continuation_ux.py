from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class PostDuplicateContinuationUxTests(SimpleTestCase):
    def setUp(self):
        root = Path(settings.BASE_DIR)
        self.template = (
            root / "data_imports" / "templates" / "data_imports" / "job_detail.html"
        ).read_text(encoding="utf-8")
        self.views = (root / "data_imports" / "views.py").read_text(encoding="utf-8")

    def test_ready_post_duplicate_modal_has_clear_next_action(self):
        self.assertIn("Continuar con la importación", self.template)
        self.assertIn("Volver al análisis", self.template)
        self.assertIn("post-duplicate-summary", self.template)

    def test_ready_post_duplicate_state_hides_processing_controls(self):
        self.assertIn(
            'document.getElementById("import-progress-counter").style.display = "none"',
            self.template,
        )
        self.assertIn(
            'document.getElementById("import-progress-background-btn").style.display = "none"',
            self.template,
        )

    def test_continue_targets_real_customer_confirmation_form(self):
        self.assertIn('id="customer-confirm-import-form"', self.template)
        self.assertIn("continuarImportacionDesdeDuplicados", self.template)
        self.assertIn(
            "{% url 'data_imports:confirm_customer_import' job.pk %}",
            self.template,
        )

    def test_manual_resolution_redirects_with_ready_outcome(self):
        self.assertIn('?resolved=1&outcome=ready', self.views)

    def test_real_async_import_modal_is_preserved(self):
        self.assertIn("iniciarImportacionConModal", self.template)
        self.assertIn("iniciarPollingImportacion", self.template)
        self.assertIn("Seguir en segundo plano", self.template)
