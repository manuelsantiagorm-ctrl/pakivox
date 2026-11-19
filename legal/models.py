from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError

class LegalAcceptance(models.Model):
    PURPOSE_REGISTRATION = "registration"
    PURPOSE_CHOICES = [(PURPOSE_REGISTRATION, "Registro de empresa")]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="legal_acceptances",
    )
    company = models.ForeignKey(
        "companies.Company",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="legal_acceptances",
    )
    pending_registration = models.ForeignKey(
        "accounts.PendingCompanyRegistration",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="legal_acceptances",
    )
    document_slug = models.SlugField(max_length=80)
    document_version = models.CharField(max_length=40)
    document_sha256 = models.CharField(max_length=64)
    purpose = models.CharField(
        max_length=30,
        choices=PURPOSE_CHOICES,
        default=PURPOSE_REGISTRATION,
    )
    accepted_at = models.DateTimeField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-accepted_at", "document_slug"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "pending_registration",
                    "document_slug",
                    "document_version",
                    "purpose",
                ],
                name="legal_unique_pending_acceptance",
            )
        ]
        indexes = [
            models.Index(
                fields=["company", "document_slug", "-accepted_at"],
                name="legal_company_doc_idx",
            )
        ]

    def __str__(self):
        return f"{self.document_slug} · {self.document_version}"

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError(
                "Las aceptaciones legales son inmutables; no se editan, "
                "solo se crean nuevas."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Las aceptaciones legales son evidencia legal y no se eliminan."
        )
