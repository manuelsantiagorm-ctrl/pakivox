import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from companies.models import Company

class Plan(models.Model):
    BILLING_PERIOD_CHOICES = [
        ("monthly", "Mensual"),
        ("yearly", "Anual"),
    ]

    SHIPPING_LEVEL_CHOICES = [
        ("manual", "Manual"),
        ("tracking", "Guías y seguimiento"),
        ("full", "Shipping Hub completo"),
    ]

    ANALYTICS_LEVEL_CHOICES = [
        ("basic", "Básico"),
        ("intermediate", "Intermedio"),
        ("advanced", "Avanzado"),
    ]

    name = models.CharField(
        "Nombre del plan",
        max_length=100,
    )

    slug = models.SlugField(
        "Slug",
        unique=True,
    )

    description = models.TextField(
        "Descripción",
        blank=True,
        null=True,
    )
    currency = models.CharField(
        "Moneda",
        max_length=3,
        default="MXN",
    )

    price = models.DecimalField(
        "Precio",
        max_digits=12,
        decimal_places=2,
        default=0,
    )
    currency = models.CharField(
        "Moneda",
        max_length=3,
        default="MXN",
        help_text="Código ISO 4217 de la moneda en que se cobra este plan.",
    )

    billing_period = models.CharField(
        "Periodo de cobro",
        max_length=20,
        choices=BILLING_PERIOD_CHOICES,
        default="monthly",
    )
    stripe_price_id = models.CharField(
        "Stripe Price ID",
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text=(
            "Price ID exacto de Stripe correspondiente a este plan. "
            "Se valida en backend antes de activar funciones Premium."
        ),
    )

    # Límites generales del SaaS
    max_users = models.PositiveIntegerField(
        "Máximo de usuarios",
        default=5,
    )

    max_products = models.PositiveIntegerField(
        "Máximo de productos",
        default=1000,
        null=True,
        blank=True,
        help_text="Vacío = productos sin límite.",
    )

    max_warehouses = models.PositiveIntegerField(
        "Máximo de bodegas",
        default=1,
    )

    # Módulo WhatsApp / Messaging Premium
    has_whatsapp = models.BooleanField(
        "Incluye WhatsApp",
        default=False,
    )
    max_whatsapp_numbers = models.PositiveIntegerField(
    "Máximo global de números de WhatsApp",
    default=0,
    help_text=(
        "Límite global de seguridad para números de WhatsApp. "
        "Se conserva por compatibilidad. La regla comercial principal "
        "se controla por bodega."
        ),
    )
    max_whatsapp_agents = models.PositiveIntegerField(
        "Máximo global de agentes de WhatsApp",
        default=0,
        help_text=(
            "Límite global de seguridad conservado por compatibilidad. "
            "La regla comercial principal se controla por número."
            ),
        )
    max_whatsapp_numbers_per_warehouse = models.PositiveIntegerField(
        "Máximo de números WhatsApp por bodega",
        default=0,
        help_text=(
            "Cantidad máxima de números de WhatsApp que una bodega "
            "puede utilizar simultáneamente."
            ),
        )
    max_whatsapp_agents_per_number = models.PositiveIntegerField(
        "Máximo de agentes por número WhatsApp",
        default=0,
        help_text=(
            "Cantidad máxima de agentes activos que pueden estar "
            "asignados a un mismo número de WhatsApp."
            ),
        )

    max_whatsapp_messages_per_month = models.PositiveIntegerField(
        "Máximo de mensajes de WhatsApp por mes",
        blank=True,
        null=True,
        default=None,
        help_text=(
            "Tope de mensajes de WhatsApp (envío/recepción) que este plan "
            "permite por mes, contado desde send_whatsapp_text(). "
            "Vacío significa sin tope definido."
        ),
    )

    whatsapp_overage_budget_mxn = models.DecimalField(
        "Tope de gasto de WhatsApp (MXN)",
        max_digits=8,
        decimal_places=2,
        blank=True,
        null=True,
        default=None,
        help_text=(
            "Tope de GASTO TOTAL en pesos que PAKIVOX absorbe al mes por "
            "empresa en WhatsApp, sin importar cuántos números tenga: "
            "comisión del proveedor (Twilio) por cada mensaje más la "
            "tarifa de Meta de los mensajes que pasan de 1,000 por número. "
            "0 bloquea todo mensaje con costo. Vacío significa sin tope: "
            "el sistema solo cuenta y no bloquea."
        ),
    )

    can_create_orders_from_chat = models.BooleanField(
        "Puede crear pedidos desde chat",
        default=False,
        help_text=(
            "Permite usar el botón Express para crear pedidos " "desde conversaciones."
        ),
    )

    can_supervise_whatsapp = models.BooleanField(
        "Puede supervisar WhatsApp",
        default=False,
        help_text=("Permite supervisión avanzada de agentes " "y conversaciones."),
    )

    # Inteligencia artificial
    has_ai = models.BooleanField(
        "Incluye IA",
        default=False,
    )

    max_ai_queries = models.PositiveIntegerField(
        "Máximo de consultas IA por periodo",
        blank=True,
        null=True,
        default=None,
        help_text=(
            "Vacío significa consultas ilimitadas. "
            "El Plan Trial debe tener un límite definido."
        ),
    )

    # Otros módulos premium
    has_api = models.BooleanField(
        "Incluye API",
        default=False,
    )

    has_multiwarehouse = models.BooleanField(
        "Incluye multi-bodega",
        default=False,
    )

    # Envíos y Análisis
    shipping_level = models.CharField(
        "Nivel de envíos",
        max_length=20,
        choices=SHIPPING_LEVEL_CHOICES,
        default="manual",
        help_text=(
            "Nivel de funcionalidad de envíos incluido en este plan: "
            "manual, guías y seguimiento, o Shipping Hub completo."
        ),
    )

    analytics_level = models.CharField(
        "Nivel de análisis",
        max_length=20,
        choices=ANALYTICS_LEVEL_CHOICES,
        default="basic",
        help_text=(
            "Nivel de análisis consolidado incluido en este plan: "
            "básico, intermedio o avanzado."
        ),
    )

    is_active = models.BooleanField(
        "Activo",
        default=True,
    )

    created_at = models.DateTimeField(
        "Fecha de creación",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        "Última actualización",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Plan"
        verbose_name_plural = "Planes"
        ordering = ["price"]

    def __str__(self):
        return f"{self.name} - ${self.price}"


class Subscription(models.Model):
    STATUS_TRIAL = "trial"
    STATUS_ACTIVE = "active"
    STATUS_PAST_DUE = "past_due"
    STATUS_SUSPENDED = "suspended"
    STATUS_CANCELLED = "cancelled"
    STATUS_EXPIRED = "expired"

    STATUS_CHOICES = [
        (STATUS_TRIAL, "Prueba"),
        (STATUS_ACTIVE, "Activa"),
        (STATUS_PAST_DUE, "Pago vencido"),
        (STATUS_SUSPENDED, "Suspendida"),
        (STATUS_CANCELLED, "Cancelada"),
        (STATUS_EXPIRED, "Expirada"),
    ]

    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name="subscription",
    )

    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name="subscriptions",
    )

    status = models.CharField(
        "Estado",
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_TRIAL,
        db_index=True,
    )

    start_date = models.DateField(
        "Fecha de inicio",
        default=timezone.now,
    )

    end_date = models.DateField(
        "Fecha de fin",
        blank=True,
        null=True,
    )

    next_billing_date = models.DateField(
        "Próxima fecha de cobro",
        blank=True,
        null=True,
    )

    trial_ends_at = models.DateField(
        "Fin de prueba",
        blank=True,
        null=True,
    )

    auto_renew = models.BooleanField(
        "Renovación automática",
        default=True,
    )

    stripe_customer_id = models.CharField(
        "Stripe Customer ID",
        max_length=200,
        blank=True,
        null=True,
        db_index=True,
    )

    stripe_subscription_id = models.CharField(
        "Stripe Subscription ID",
        max_length=200,
        blank=True,
        null=True,
        db_index=True,
    )
    stripe_verified_at = models.DateTimeField(
        "Última verificación válida de Stripe",
        blank=True,
        null=True,
        help_text=(
            "Solo debe actualizarse después de procesar un webhook firmado "
            "de Stripe que confirme el pago."
        ),
    )

    stripe_last_paid_event_id = models.CharField(
        "Último evento pagado de Stripe",
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Identificador único del último evento firmado de Stripe "
            "que confirmó el acceso."
        ),
    )
    stripe_verified_price_id = models.CharField(
        "Price ID verificado por Stripe",
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Price ID obtenido del evento firmado que confirmó el último pago."
        ),
    )

    mercadopago_subscription_id = models.CharField(
        "Mercado Pago Subscription ID",
        max_length=200,
        blank=True,
        null=True,
    )

    # Consumo de Gemini durante el periodo actual
    ai_queries_used = models.PositiveIntegerField(
        "Consultas IA utilizadas",
        default=0,
    )

    ai_period_started_at = models.DateField(
        "Inicio del periodo IA",
        blank=True,
        null=True,
    )

    ai_period_ends_at = models.DateField(
        "Fin del periodo IA",
        blank=True,
        null=True,
    )

    whatsapp_messages_used = models.PositiveIntegerField(
        "Mensajes de WhatsApp usados",
        default=0,
    )

    whatsapp_period_started_at = models.DateField(
        "Inicio del periodo de mensajes WhatsApp",
        blank=True,
        null=True,
    )

    whatsapp_period_ends_at = models.DateField(
        "Fin del periodo de mensajes WhatsApp",
        blank=True,
        null=True,
    )

    whatsapp_overage_authorized_at = models.DateTimeField(
        "Excedente de WhatsApp autorizado",
        blank=True,
        null=True,
        help_text=(
            "Fecha en que el owner autorizó continuar enviando mensajes "
            "por encima del tope mensual, sabiendo que el excedente se "
            "cobrará aparte. Vacío significa que no ha autorizado."
        ),
    )

    whatsapp_overage_notified_at = models.DateTimeField(
        "Alerta de excedente de WhatsApp generada",
        blank=True,
        null=True,
        help_text=(
            "Fecha en que se generó la alerta al owner por haber cruzado "
            "el tope de mensajes del periodo. Vacío significa que todavía "
            "no se ha generado ninguna alerta para el periodo actual."
        ),
    )

    created_at = models.DateTimeField(
        "Fecha de creación",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        "Última actualización",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Suscripción"
        verbose_name_plural = "Suscripciones"
        indexes = [
            models.Index(
                fields=["status", "end_date"],
                name="subscription_status_end_idx",
            ),
        ]

    def __str__(self):
        return f"{self.company.name} - {self.plan.name}"

    @property
    def is_trial(self):
        return self.status == self.STATUS_TRIAL

    @property
    def is_trial_expired(self):
        if not self.is_trial:
            return False

        if not self.trial_ends_at:
            return True

        return self.trial_ends_at < timezone.localdate()

    @property
    def is_valid(self):
        today = timezone.localdate()

        if self.status == self.STATUS_TRIAL:
            if not self.trial_ends_at:
                return False

            return self.trial_ends_at >= today

        if self.status == self.STATUS_ACTIVE:
            if self.end_date and self.end_date < today:
                return False

            return True

        return False

    @property
    def is_read_only(self):
        return not self.is_valid

    @property
    def has_whatsapp_access(self):
        """
        WhatsApp Premium únicamente está disponible cuando Stripe confirmó
        una suscripción activa y pagada.

        Cambiar el plan, el estado o un botón visual no habilita el módulo.
        """
        return bool(
            self.status == self.STATUS_ACTIVE
            and not self.is_trial
            and self.is_valid
            and self.plan_id
            and self.plan.is_active
            and self.plan.has_whatsapp
            and self.stripe_customer_id
            and self.stripe_subscription_id
            and self.stripe_verified_at
            and self.stripe_last_paid_event_id
            and self.plan.stripe_price_id
            and self.stripe_verified_price_id
            and self.stripe_verified_price_id == self.plan.stripe_price_id
        )
    @property
    def max_whatsapp_numbers(self):
        return self.plan.max_whatsapp_numbers

    @property
    def max_whatsapp_agents(self):
        return self.plan.max_whatsapp_agents
    
    @property
    def max_whatsapp_numbers_per_warehouse(self):
        if not self.plan_id:
            return 0
        return self.plan.max_whatsapp_numbers_per_warehouse
    
    @property
    def max_whatsapp_agents_per_number(self):
        if not self.plan_id:
            return 0
        return self.plan.max_whatsapp_agents_per_number

    @property
    def can_create_orders_from_chat(self):
        return bool(
            self.has_whatsapp_access
            and self.plan.can_create_orders_from_chat
        )

    @property
    def can_supervise_whatsapp(self):
        return bool(
            self.has_whatsapp_access
            and self.plan.can_supervise_whatsapp
        )

    @property
    def has_ai_access(self):
        return self.is_valid and self.plan.has_ai

    @property
    def ai_queries_limit(self):
        return self.plan.max_ai_queries

    @property
    def ai_queries_remaining(self):
        limit = self.ai_queries_limit

        if limit is None:
            return None

        return max(limit - self.ai_queries_used, 0)

    @property
    def has_ai_queries_available(self):
        if not self.has_ai_access:
            return False

        limit = self.ai_queries_limit

        if limit is None:
            return True

        return self.ai_queries_used < limit

    @property
    def whatsapp_messages_limit(self):
        if not self.plan_id:
            return None
        return self.plan.max_whatsapp_messages_per_month

    @property
    def whatsapp_messages_remaining(self):
        limit = self.whatsapp_messages_limit

        if limit is None:
            return None

        return max(limit - self.whatsapp_messages_used, 0)

    @property
    def is_whatsapp_overage_authorized(self):
        return bool(self.whatsapp_overage_authorized_at)


class AIQueryUsage(models.Model):
    STATUS_RESERVED = "reserved"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_REFUNDED = "refunded"

    STATUS_CHOICES = [
        (STATUS_RESERVED, "Reservada"),
        (STATUS_SUCCEEDED, "Exitosa"),
        (STATUS_FAILED, "Fallida"),
        (STATUS_REFUNDED, "Devuelta"),
    ]

    request_id = models.UUIDField(
        "ID de solicitud",
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="ai_query_usages",
    )

    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="ai_query_usages",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="ai_query_usages",
    )

    mode = models.CharField(
        "Tipo de consulta",
        max_length=40,
        blank=True,
        default="full",
    )

    warehouse_scope = models.CharField(
        "Alcance de bodegas",
        max_length=120,
        blank=True,
        default="all",
    )

    question_hash = models.CharField(
        "Hash de la pregunta",
        max_length=64,
        blank=True,
        default="",
        db_index=True,
    )

    status = models.CharField(
        "Estado",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_RESERVED,
        db_index=True,
    )

    error_code = models.CharField(
        "Código de error",
        max_length=80,
        blank=True,
        default="",
    )

    reserved_at = models.DateTimeField(
        "Fecha de reserva",
        auto_now_add=True,
    )

    completed_at = models.DateTimeField(
        "Fecha de finalización",
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = "Consumo de consulta IA"
        verbose_name_plural = "Consumos de consultas IA"
        ordering = ["-reserved_at"]
        indexes = [
            models.Index(
                fields=["company", "status", "reserved_at"],
                name="ai_usage_company_status_idx",
            ),
            models.Index(
                fields=["subscription", "reserved_at"],
                name="ai_usage_subscription_idx",
            ),
        ]

    def __str__(self):
        return f"{self.company} · {self.mode} · " f"{self.get_status_display()}"


class StripeWebhookEvent(models.Model):
    STATUS_PROCESSING = "processing"
    STATUS_PROCESSED = "processed"
    STATUS_FAILED = "failed"
    STATUS_IGNORED = "ignored"

    STATUS_CHOICES = [
        (STATUS_PROCESSING, "Procesando"),
        (STATUS_PROCESSED, "Procesado"),
        (STATUS_FAILED, "Fallido"),
        (STATUS_IGNORED, "Ignorado"),
    ]

    event_id = models.CharField(
        "Stripe Event ID",
        max_length=255,
        unique=True,
    )

    event_type = models.CharField(
        "Tipo de evento",
        max_length=120,
        db_index=True,
    )

    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="stripe_webhook_events",
    )

    status = models.CharField(
        "Estado",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PROCESSING,
        db_index=True,
    )

    error_message = models.TextField(
        "Mensaje de error",
        blank=True,
        default="",
    )

    received_at = models.DateTimeField(
        "Fecha de recepción",
        auto_now_add=True,
    )

    processed_at = models.DateTimeField(
        "Fecha de procesamiento",
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = "Evento webhook de Stripe"
        verbose_name_plural = "Eventos webhook de Stripe"
        ordering = ["-received_at"]
        indexes = [
            models.Index(
                fields=["status", "received_at"],
                name="stripe_event_status_idx",
            ),
        ]

    def __str__(self):
        return f"{self.event_type} · {self.event_id}"


class SubscriptionPayment(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ("stripe", "Stripe"),
        ("mercadopago", "Mercado Pago"),
        ("transfer", "Transferencia"),
        ("manual", "Manual"),
    ]

    STATUS_CHOICES = [
        ("pending", "Pendiente"),
        ("paid", "Pagado"),
        ("failed", "Fallido"),
        ("refunded", "Reembolsado"),
        ("cancelled", "Cancelado"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="subscription_payments",
    )

    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name="payments",
    )

    folio = models.CharField(
        "Folio",
        max_length=80,
    )

    amount = models.DecimalField(
        "Monto",
        max_digits=12,
        decimal_places=2,
    )

    payment_method = models.CharField(
        "Método de pago",
        max_length=30,
        choices=PAYMENT_METHOD_CHOICES,
        default="manual",
    )

    status = models.CharField(
        "Estado",
        max_length=30,
        choices=STATUS_CHOICES,
        default="pending",
    )

    transaction_id = models.CharField(
        "ID de transacción",
        max_length=200,
        blank=True,
        null=True,
    )

    paid_at = models.DateTimeField(
        "Fecha de pago",
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        "Fecha de creación",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        "Última actualización",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Pago de suscripción"
        verbose_name_plural = "Pagos de suscripción"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "folio"],
                name="unique_subscription_payment_folio",
            ),
        ]

    def __str__(self):
        return f"{self.folio} - " f"{self.company.name} - " f"{self.amount}"
