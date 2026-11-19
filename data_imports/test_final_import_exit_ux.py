from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class FinalImportExitUxTests(SimpleTestCase):
    def setUp(self):
        path = (
            Path(settings.BASE_DIR)
            / "data_imports"
            / "templates"
            / "data_imports"
            / "job_detail.html"
        )
        self.source = path.read_text(encoding="utf-8")

    def test_completed_modal_has_real_exit(self):
        self.assertIn("Finalizar y volver a importaciones", self.source)
        self.assertIn("final-import-actions", self.source)

    def test_completed_modal_hides_background_button(self):
        self.assertIn(
            'document.getElementById("import-progress-background-btn").style.display = "none"',
            self.source,
        )

    def test_failed_rows_have_direct_resolution_action(self):
        self.assertIn(
            "{% url 'data_imports:pending_customers' job.pk %}",
            self.source,
        )
        self.assertIn("no importados", self.source)

    def test_completed_page_also_has_exit_buttons(self):
        self.assertIn("completed-banner-actions", self.source)
        self.assertIn("{% url 'data_imports:dashboard' %}", self.source)

    def test_existing_async_progress_is_preserved(self):
        self.assertIn("iniciarPollingImportacion", self.source)
        self.assertIn("import-progress-counter", self.source)
