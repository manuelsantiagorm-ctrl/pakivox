from django.conf import settings
from django.db import models


class Notification(models.Model):
    LEVEL_INFO = "info"
    LEVEL_SUCCESS = "success"
    LEVEL_WARNING = "warning"
    LEVEL_CRITICAL = "critical"

    LEVEL_CHOICES = [
        (LEVEL_INFO, "Información"),
        (LEVEL_SUCCESS, "Éxito"),
        (LEVEL_WARNING, "Advertencia"),
        (LEVEL_CRITICAL, "Crítica"),
    ]

    STATUS_ACTIVE = "active"
    STATUS_RESOLVED = "resolved"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Activa"),
        (STATUS_RESOLVED, "Resuelta"),
    ]

    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    warehouse = models.ForeignKey(
        "inventory.Warehouse",
        on_delete=models.CASCADE,
        related_name="notifications",
        blank=True,
        null=True,
    )
    category = models.CharField(max_length=40, db_index=True)
    event_type = models.CharField(max_length=60, db_index=True)
    level = models.CharField(
        max_length=20,
        choices=LEVEL_CHOICES,
        default=LEVEL_INFO,
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True,
    )
    title = models.CharField(max_length=180)
    message = models.TextField()
    icon = models.CharField(max_length=60, default="bi-bell")
    action_label = models.CharField(max_length=80, default="Ver")
    action_name = models.CharField(
        max_length=180,
        help_text="Nombre interno de URL de Django.",
    )
    action_kwargs = models.JSONField(default=dict, blank=True)
    source_app = models.CharField(max_length=60)
    source_model = models.CharField(max_length=80)
    source_object_id = models.PositiveBigIntegerField()
    dedupe_key = models.CharField(max_length=220)
    reference = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-updated_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "dedupe_key"],
                condition=models.Q(status="active"),
                name="uniq_active_notification_company_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "status", "level"],
                name="notif_company_status_level_idx",
            ),
            models.Index(
                fields=["source_app", "source_model", "source_object_id"],
                name="notif_source_object_idx",
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        super().clean()

        if self.warehouse_id and self.company_id:
            if self.warehouse.company_id != self.company_id:
                raise ValidationError(
                    {"warehouse": ("La bodega seleccionada pertenece a otra empresa.")}
                )

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"


class NotificationUserState(models.Model):
    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name="user_states",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_states",
    )
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["notification", "user"],
                name="uniq_notification_state_user",
            ),
        ]
