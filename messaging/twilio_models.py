"""
Subcuenta de Twilio por empresa (migracion a Twilio, Paso 3a).

Decision de Manuel (25-sep-2026): PAKIVOX tiene UNA cuenta principal de
Twilio y crea por dentro una SUBCUENTA por empresa cliente. El cliente
nunca crea cuenta en Twilio, nunca la ve y nunca le paga: Twilio le
factura todo a PAKIVOX, separado por subcuenta.

Por que una subcuenta por empresa:
  - Aislamiento: los numeros, mensajes y credenciales de una empresa no
    comparten cuenta con los de otra.
  - Gasto por cliente: Twilio reporta el consumo de cada subcuenta.
  - Twilio exige que cada cuenta de WhatsApp Business (WABA) quede ligada a
    UNA sola cuenta o subcuenta.

Ver claude/twilio-migracion-checklist.md.
"""

from django.db import models
from django.utils import timezone

from companies.models import Company

from .crypto_fields import EncryptedTextField


class TwilioSubaccount(models.Model):
    STATUS_PENDING = "pending"
    STATUS_ACTIVE = "active"
    STATUS_SUSPENDED = "suspended"
    STATUS_CLOSED = "closed"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pendiente de crear en Twilio"),
        (STATUS_ACTIVE, "Activa"),
        (STATUS_SUSPENDED, "Suspendida"),
        (STATUS_CLOSED, "Cerrada"),
        (STATUS_FAILED, "Fallo al crear"),
    ]

    company = models.OneToOneField(
        Company,
        on_delete=models.PROTECT,
        related_name="twilio_subaccount",
        verbose_name="Empresa",
    )

    account_sid = models.CharField(
        "Account SID de la subcuenta",
        max_length=64,
        blank=True,
        default="",
        db_index=True,
    )

    auth_token = EncryptedTextField(
        "Auth Token de la subcuenta",
        blank=True,
        default="",
    )

    friendly_name = models.CharField(
        "Nombre en Twilio",
        max_length=128,
        help_text=(
            "Nombre con el que PAKIVOX identifica la subcuenta en Twilio. "
            "Se usa para no crear dos subcuentas para la misma empresa."
        ),
    )

    status = models.CharField(
        "Estado",
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )

    last_error = models.TextField(
        "Ultimo error",
        blank=True,
        default="",
    )

    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "messaging"
        verbose_name = "Subcuenta de Twilio"
        verbose_name_plural = "Subcuentas de Twilio"
        constraints = [
            models.UniqueConstraint(
                fields=["account_sid"],
                condition=~models.Q(account_sid=""),
                name="uniq_twilio_subaccount_sid",
            ),
        ]

    def __str__(self):
        return f"{self.friendly_name} ({self.get_status_display()})"

    @property
    def is_ready(self):
        return (
            self.status == self.STATUS_ACTIVE
            and bool(self.account_sid)
            and bool(self.auth_token)
        )
