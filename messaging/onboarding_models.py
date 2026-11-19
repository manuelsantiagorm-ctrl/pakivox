from django.conf import settings
from django.db import models


class OnboardingStatusEvent(models.Model):
    whatsapp_number = models.ForeignKey(
        "messaging.WhatsAppNumber",
        on_delete=models.CASCADE,
        related_name="onboarding_events",
    )
    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.CASCADE,
        related_name="whatsapp_onboarding_events",
    )
    warehouse = models.ForeignKey(
        "inventory.Warehouse",
        on_delete=models.PROTECT,
        related_name="whatsapp_onboarding_events",
    )
    previous_status = models.CharField(max_length=30)
    new_status = models.CharField(max_length=30)
    public_note = models.TextField(blank=True, default="")
    internal_note = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="whatsapp_onboarding_events_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "messaging"
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(
                fields=["company", "created_at"],
                name="messaging_o_company_3f52f0_idx",
            ),
            models.Index(
                fields=["whatsapp_number", "created_at"],
                name="messaging_o_whatsap_5b429c_idx",
            ),
            models.Index(
                fields=["new_status", "created_at"],
                name="messaging_o_new_sta_a7bc1c_idx",
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if self.whatsapp_number_id:
            if self.company_id != self.whatsapp_number.company_id:
                errors["company"] = "La empresa no coincide con la del canal."
            if self.warehouse_id != self.whatsapp_number.warehouse_id:
                errors["warehouse"] = "La bodega no coincide con la del canal."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
