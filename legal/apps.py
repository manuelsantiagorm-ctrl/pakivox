from django.apps import AppConfig


class LegalConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "legal"
    verbose_name = "Cumplimiento legal"

    def ready(self):
        from legal import checks  # noqa: F401
