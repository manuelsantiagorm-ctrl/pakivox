import secrets

from django.conf import settings
from django.db import models
from django.db.models import Q

from companies.scoping import CompanyScopedModel


def generate_tracking_token():
    # 24 bytes aleatorios -> 32 caracteres URL-safe (~192 bits).
    return secrets.token_urlsafe(24)


class OrderTrackingLink(CompanyScopedModel):
    """
    Link público y secreto para que el comprador vea su pedido sin sesión.

    El token se guarda en claro porque PAKIVOX necesita volver a ponerlo en
    correos y borradores de WhatsApp. Quien tenga el link solo ve el estado
    del pedido de esa empresa; renovar el token invalida el anterior.
    """

    order = models.OneToOneField(
        "sales.Order",
        on_delete=models.CASCADE,
        related_name="tracking_link",
    )
    token = models.CharField(
        "Token",
        max_length=64,
        unique=True,
        default=generate_tracking_token,
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)
    last_viewed_at = models.DateTimeField(null=True, blank=True)
    view_count = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Link de seguimiento"
        verbose_name_plural = "Links de seguimiento"
        indexes = [
            models.Index(fields=["company", "created_at"], name="cu_track_company_idx"),
        ]

    def __str__(self):
        return f"Seguimiento del pedido {self.order_id}"


class CustomerEmailNotification(CompanyScopedModel):
    """
    Bitácora e idempotencia de los correos al comprador.

    Una fila por (empresa, dedupe_key). La fila se crea en la misma
    transacción del cambio de estado, así que si Redis o Celery fallan el
    aviso no se pierde: la tarea periódica lo vuelve a encolar.
    """

    EVENT_ORDER_CONFIRMED = "order_confirmed"
    EVENT_PAYMENT_APPROVED = "payment_approved"
    EVENT_ORDER_SHIPPED = "order_shipped"
    EVENT_ORDER_DELIVERED = "order_delivered"
    EVENT_MANUAL = "manual"

    EVENT_CHOICES = [
        (EVENT_ORDER_CONFIRMED, "Pedido registrado"),
        (EVENT_PAYMENT_APPROVED, "Pago aprobado"),
        (EVENT_ORDER_SHIPPED, "Pedido enviado"),
        (EVENT_ORDER_DELIVERED, "Pedido entregado"),
        (EVENT_MANUAL, "Envío manual"),
    ]

    STATUS_PENDING = "pending"
    STATUS_SENDING = "sending"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"

    STATUS_CHOICES = [
        (STATUS_PENDING, "En cola"),
        (STATUS_SENDING, "Enviando"),
        (STATUS_SENT, "Enviado"),
        (STATUS_FAILED, "Falló"),
        (STATUS_SKIPPED, "No se envió"),
    ]

    MAX_ATTEMPTS = 5

    order = models.ForeignKey(
        "sales.Order",
        on_delete=models.CASCADE,
        related_name="customer_email_notifications",
    )
    payment = models.ForeignKey(
        "payments.Payment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    event_type = models.CharField(max_length=30, choices=EVENT_CHOICES)
    dedupe_key = models.CharField(max_length=120)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    recipient = models.EmailField(blank=True)
    subject = models.CharField(max_length=200, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    claimed_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Correo al cliente"
        verbose_name_plural = "Correos al cliente"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "dedupe_key"],
                name="cu_email_company_dedupe_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "order", "-created_at"], name="cu_email_order_idx"),
            models.Index(
                fields=["status", "updated_at"],
                name="cu_email_retry_idx",
                condition=Q(status__in=["pending", "sending", "failed"]),
            ),
        ]

    def __str__(self):
        return f"{self.get_event_type_display()} · pedido {self.order_id}"


class WhatsAppDraftLog(CompanyScopedModel):
    """
    Registro de cada borrador que un vendedor abrió en WhatsApp (wa.me).

    PAKIVOX no puede saber si el vendedor presionó "enviar" en su celular;
    lo que sí sabe es que abrió el chat con ese texto y ese número.
    """

    order = models.ForeignKey(
        "sales.Order",
        on_delete=models.CASCADE,
        related_name="whatsapp_draft_logs",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    draft_key = models.CharField(max_length=40)
    phone = models.CharField(max_length=20)
    phone_source = models.CharField(max_length=40, blank=True)
    body = models.TextField()
    was_edited = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Borrador de WhatsApp abierto"
        verbose_name_plural = "Borradores de WhatsApp abiertos"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "order", "-created_at"], name="cu_wadraft_order_idx"),
        ]

    def __str__(self):
        return f"WhatsApp {self.draft_key} · pedido {self.order_id}"
