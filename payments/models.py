from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import Sum
from django.utils import timezone

from companies.models import Company
from sales.models import Order

class Payment(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ("cash", "Efectivo"),
        ("transfer", "Transferencia"),
        ("card", "Tarjeta"),
        ("mercadopago", "Mercado Pago"),
        ("stripe", "Stripe"),
        ("conekta", "Conekta"),
        ("manual", "Manual"),
    ]

    PAYMENT_TYPE_CHOICES = [
        ("full", "Pago completo"),
        ("deposit", "Anticipo"),
        ("remaining", "Pago restante"),
    ]

    STATUS_CHOICES = [
        ("pending", "Pendiente"),
        ("under_review", "En revisión"),
        ("approved", "Aprobado"),
        ("rejected", "Rechazado"),
        ("refunded", "Reembolsado"),
        ("cancelled", "Cancelado"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="payments",
    )

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="payments",
    )

    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="registered_payments",
    )

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="reviewed_payments",
    )

    folio = models.CharField("Folio de pago", max_length=50)

    payment_method = models.CharField(
        "Método de pago",
        max_length=30,
        choices=PAYMENT_METHOD_CHOICES,
    )

    payment_type = models.CharField(
        "Tipo de pago",
        max_length=30,
        choices=PAYMENT_TYPE_CHOICES,
    )

    status = models.CharField(
        "Estado",
        max_length=30,
        choices=STATUS_CHOICES,
        default="pending",
    )

    amount = models.DecimalField(
        "Monto",
        max_digits=12,
        decimal_places=2,
    )

    proof_file = models.FileField(
        "Comprobante",
        upload_to="private/payment_proofs/",
        blank=True,
        null=True,
    )

    transaction_id = models.CharField(
        "ID de transacción",
        max_length=150,
        blank=True,
        null=True,
    )

    notes = models.TextField(
        "Notas",
        blank=True,
        null=True,
    )

    rejection_reason = models.TextField(
        "Motivo de rechazo",
        blank=True,
        null=True,
    )

    paid_at = models.DateTimeField(
        "Fecha de pago",
        blank=True,
        null=True,
    )

    reviewed_at = models.DateTimeField(
        "Fecha de revisión",
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        "Fecha de registro",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        "Última actualización",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Pago"
        verbose_name_plural = "Pagos"
        ordering = ["-created_at"]
        unique_together = ("company", "folio")

    def __str__(self):
        return f"{self.folio} - {self.order.folio} - ${self.amount}"

    @property
    def can_approve(self):
        return self.status in ["pending", "under_review"]

    @property
    def can_reject(self):
        return self.status in ["pending", "under_review"]

    @property
    def can_cancel(self):
        return self.status in ["pending", "under_review"]

    @property
    def can_refund(self):
        return self.status == "approved"

    def clean(self):
        if self.order_id and self.company_id:
            if self.order.company_id != self.company_id:
                raise ValidationError(
                    "La empresa del pago no coincide con la empresa del pedido."
                )

        if self.amount is not None and self.amount <= 0:
            raise ValidationError("El monto del pago debe ser mayor a cero.")

    def save(self, *args, **kwargs):
        if self.folio or not self.company_id:
            return super().save(*args, **kwargs)

        local_date = timezone.localdate()
        prefix = f"PAY-{local_date.strftime('%y%m%d')}-"

        for attempt in range(5):
            existing_folios = (
                Payment.objects.filter(
                    company_id=self.company_id,
                    folio__startswith=prefix,
                )
                .values_list("folio", flat=True)
            )

            highest_sequence = 0

            for existing_folio in existing_folios:
                try:
                    sequence = int(existing_folio.rsplit("-", 1)[1])
                except (AttributeError, IndexError, TypeError, ValueError):
                    continue

                highest_sequence = max(
                    highest_sequence,
                    sequence,
                )

            self.folio = (
                f"{prefix}{highest_sequence + 1:04d}"
            )

            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError:
                self.folio = ""

                if attempt == 4:
                    raise

    def sync_order_payment_status(self):
        """Sincroniza la cobertura del pedido desde una única fuente financiera.

        Desde CS-1C4 ``amount_paid`` representa el importe cubierto del pedido:
        pagos APPROVED + saldo a favor aplicado. El efectivo real sigue viviendo
        exclusivamente en Payment; aplicar crédito nunca crea un Payment ficticio.
        """
        from payments.services.customer_credit import sync_order_financial_coverage

        decision = sync_order_financial_coverage(order=self.order)
        self.order = decision.order

    @transaction.atomic
    def approve(self, user=None):
        from decimal import Decimal

        from django.db.models import Sum

        from payments.services.customer_credit import active_order_credit_total
        from payments.services.financial_locks import lock_order_then_payment

        order, payment = lock_order_then_payment(
            company_id=self.company_id,
            payment_id=self.pk,
        )

        payment.order = order

        if not payment.can_approve:
            raise ValidationError("Este pago ya no puede aprobarse.")

        # BUG-047: separación de responsabilidades — aplica en el modelo,
        # no solo en la vista, para que también cubra el Admin de Django
        # y cualquier otro punto de entrada (shell, scripts, etc.).
        from payments.access import is_active_company_owner

        if (
            order.created_by_id == user.id if user else False
        ) and not is_active_company_owner(user, company=order.company):
            raise ValidationError(
                "Separación de responsabilidades: no puedes aprobar "
                "el pago de un pedido creado por ti."
            )

        # Re-validación de saldo (BUG-045): el pedido y el pago ya están
        # bloqueados por lock_order_then_payment, así que esta suma es
        # segura contra aprobaciones concurrentes del mismo pedido.
        already_approved = order.payments.filter(status="approved").exclude(
            pk=payment.pk
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        credit_total = active_order_credit_total(
            company_id=order.company_id, order_id=order.id
        )
        prospective_covered_total = already_approved + credit_total + payment.amount

        if prospective_covered_total > order.total:
            excedente = prospective_covered_total - order.total
            raise ValidationError(
                "Este pago excede el saldo pendiente del pedido "
                f"por ${excedente}. Total del pedido: ${order.total}, "
                f"ya cubierto: ${already_approved + credit_total}, "
                f"este pago: ${payment.amount}. Verifica el monto antes "
                "de aprobar."
            )

        payment.status = "approved"
        payment.reviewed_by = user
        payment.reviewed_at = timezone.now()

        if not payment.paid_at:
            payment.paid_at = timezone.now()

        payment.rejection_reason = ""
        payment.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "paid_at",
                "rejection_reason",
                "updated_at",
            ]
        )

        payment.sync_order_payment_status()

        # El cobro final y la reserva operativa son una sola transacción. Si no
        # puede reservarse todo el pedido, también se revierte la aprobación.
        if payment.order.status == "paid" and payment.order.balance_due <= 0:
            from warehouse.services import reserve_paid_order

            reserved_pickings = reserve_paid_order(payment.order, user=user)
            if not reserved_pickings:
                raise ValidationError(
                    "No se aprobo el pago porque no fue posible crear "
                    "la reserva operativa completa del pedido."
                )

        from dashboard.services.warehouse_cash_attribution import (
            record_locked_payment_collection,
        )

        record_locked_payment_collection(
            order=payment.order,
            payment=payment,
            actor=user,
            occurred_at=payment.reviewed_at,
        )

        for field_name in (
            "status",
            "reviewed_by",
            "reviewed_at",
            "paid_at",
            "rejection_reason",
        ):
            setattr(self, field_name, getattr(payment, field_name))
        self.order = payment.order

    @transaction.atomic
    def reject(self, user=None, reason=""):
        from payments.services.financial_locks import lock_order_then_payment

        order, payment = lock_order_then_payment(
            company_id=self.company_id,
            payment_id=self.pk,
        )
        payment.order = order
        if not payment.can_reject:
            raise ValidationError("Este pago ya no puede rechazarse.")

        payment.status = "rejected"
        payment.reviewed_by = user
        payment.reviewed_at = timezone.now()
        payment.rejection_reason = reason
        payment.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "rejection_reason",
                "updated_at",
            ]
        )

        payment.sync_order_payment_status()
        self.status = payment.status
        self.reviewed_by = payment.reviewed_by
        self.reviewed_at = payment.reviewed_at
        self.rejection_reason = payment.rejection_reason
        self.order = payment.order

    @transaction.atomic
    def cancel(self, user=None, reason=""):
        from payments.services.financial_locks import lock_order_then_payment

        order, payment = lock_order_then_payment(
            company_id=self.company_id,
            payment_id=self.pk,
        )
        payment.order = order
        if not payment.can_cancel:
            raise ValidationError("Este pago ya no puede cancelarse.")

        payment.status = "cancelled"
        payment.reviewed_by = user
        payment.reviewed_at = timezone.now()
        payment.rejection_reason = reason
        payment.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "rejection_reason",
                "updated_at",
            ]
        )

        payment.sync_order_payment_status()
        self.status = payment.status
        self.reviewed_by = payment.reviewed_by
        self.reviewed_at = payment.reviewed_at
        self.rejection_reason = payment.rejection_reason
        self.order = payment.order

    def refund(self, user=None, reason=""):
        """
        Compatibilidad defensiva.

        Desde CS-1C1 un Payment no puede cambiar a ``refunded`` mediante un
        método de modelo sin evidencia de devolución. Los reembolsos deben pasar
        por ``payments.services.refunds`` para separar solicitud y confirmación.
        """
        raise ValidationError(
            "El reembolso directo está deshabilitado. Usa el flujo de "
            "solicitud y confirmación de reembolso de PAKIVOX."
        )


class PaymentRefundRequest(models.Model):
    STATUS_REQUESTED = "requested"
    STATUS_CONFIRMED = "confirmed"
    STATUS_REJECTED = "rejected"
    STATUS_CANCELLED = "cancelled"
    STATUS_CONVERTED_TO_CREDIT = "converted_to_credit"
    STATUS_CHOICES = [
        (STATUS_REQUESTED, "Solicitado"),
        (STATUS_CONFIRMED, "Confirmado"),
        (STATUS_REJECTED, "Rechazado"),
        (STATUS_CANCELLED, "Cancelado"),
        (STATUS_CONVERTED_TO_CREDIT, "Convertido a saldo a favor"),
    ]

    METHOD_MANUAL_EXTERNAL = "manual_external"
    METHOD_CHOICES = [
        (METHOD_MANUAL_EXTERNAL, "Devolución externa confirmada manualmente"),
    ]

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="payment_refund_requests"
    )
    payment = models.ForeignKey(
        Payment, on_delete=models.PROTECT, related_name="refund_requests"
    )
    order = models.ForeignKey(
        Order, on_delete=models.PROTECT, related_name="payment_refund_requests"
    )
    customer = models.ForeignKey(
        "crm.Customer", on_delete=models.PROTECT, related_name="payment_refund_requests"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_payment_refunds",
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="confirmed_payment_refunds",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_REQUESTED, db_index=True
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(
        max_length=30, choices=METHOD_CHOICES, default=METHOD_MANUAL_EXTERNAL
    )
    reason = models.TextField()
    confirmation_reference = models.CharField(max_length=180, blank=True)
    request_idempotency_key = models.CharField(
        max_length=64, db_index=True, null=True, blank=True
    )
    confirm_idempotency_key = models.CharField(
        max_length=64, db_index=True, null=True, blank=True
    )
    requested_at = models.DateTimeField(auto_now_add=True, db_index=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-requested_at", "-id"]
        indexes = [
            models.Index(
                fields=["company", "status", "requested_at"],
                name="pay_ref_company_status_idx",
            ),
            models.Index(
                fields=["company", "payment", "status"],
                name="pay_ref_company_pay_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "request_idempotency_key"],
                condition=models.Q(request_idempotency_key__isnull=False),
                name="uniq_refund_req_idem_company",
            ),
            models.UniqueConstraint(
                fields=["company", "confirm_idempotency_key"],
                condition=models.Q(confirm_idempotency_key__isnull=False),
                name="uniq_refund_confirm_idem_company",
            ),
        ]

    def clean(self):
        if self.payment_id and self.company_id:
            if self.payment.company_id != self.company_id:
                raise ValidationError(
                    "El reembolso y el pago pertenecen a empresas distintas."
                )
        if self.order_id and self.company_id:
            if self.order.company_id != self.company_id:
                raise ValidationError(
                    "El reembolso y el pedido pertenecen a empresas distintas."
                )
        if self.payment_id and self.order_id:
            if self.payment.order_id != self.order_id:
                raise ValidationError("El pago no pertenece al pedido del reembolso.")
        if self.customer_id and self.order_id:
            if self.order.customer_id != self.customer_id:
                raise ValidationError(
                    "El cliente no coincide con el pedido del reembolso."
                )
        if self.amount is not None and self.amount <= 0:
            raise ValidationError("El monto del reembolso debe ser mayor a cero.")

    def __str__(self):
        return f"Refund {self.payment.folio} · {self.get_status_display()} · ${self.amount}"


class CustomerCredit(models.Model):
    """Saldo a favor emitido por dinero real retenido por la empresa.

    CS-1C3 NO aplica todavía el crédito a pedidos nuevos. Esta entidad es la
    fuente de verdad auditable del crédito disponible; la aplicación a una
    nueva venta se implementará sobre este ledger sin fabricar Payments.
    """

    STATUS_ACTIVE = "active"
    STATUS_EXHAUSTED = "exhausted"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Disponible"),
        (STATUS_EXHAUSTED, "Agotado"),
        (STATUS_CANCELLED, "Cancelado"),
    ]

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="customer_credits"
    )
    customer = models.ForeignKey(
        "crm.Customer", on_delete=models.PROTECT, related_name="credits"
    )
    source_payment = models.ForeignKey(
        Payment, on_delete=models.PROTECT, related_name="customer_credits"
    )
    source_order = models.ForeignKey(
        Order, on_delete=models.PROTECT, related_name="customer_credits"
    )
    source_refund_request = models.OneToOneField(
        PaymentRefundRequest,
        on_delete=models.PROTECT,
        related_name="customer_credit",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_customer_credits",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    remaining_amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True
    )
    reason = models.TextField()
    idempotency_key = models.CharField(
        max_length=64, db_index=True, null=True, blank=True
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["company", "customer", "status"],
                name="cust_credit_comp_cust_idx",
            ),
            models.Index(
                fields=["company", "source_payment"],
                name="cust_credit_comp_pay_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="cust_credit_amount_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(remaining_amount__gte=0),
                name="cust_credit_remaining_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(remaining_amount__lte=models.F("amount")),
                name="cust_credit_remaining_lte_amount",
            ),
            models.UniqueConstraint(
                fields=["company", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="uniq_cust_credit_idem_company",
            ),
        ]

    def clean(self):
        if self.company_id and self.customer_id:
            if self.customer.company_id != self.company_id:
                raise ValidationError("El saldo a favor y el cliente pertenecen a empresas distintas.")
        if self.company_id and self.source_payment_id:
            if self.source_payment.company_id != self.company_id:
                raise ValidationError("El saldo a favor y el pago pertenecen a empresas distintas.")
        if self.company_id and self.source_order_id:
            if self.source_order.company_id != self.company_id:
                raise ValidationError("El saldo a favor y el pedido pertenecen a empresas distintas.")
        if self.source_payment_id and self.source_order_id:
            if self.source_payment.order_id != self.source_order_id:
                raise ValidationError("El pago no pertenece al pedido origen del saldo a favor.")
        if self.customer_id and self.source_order_id:
            if self.source_order.customer_id != self.customer_id:
                raise ValidationError("El cliente no coincide con el pedido origen.")
        if self.source_refund_request_id and self.source_payment_id:
            if self.source_refund_request.payment_id != self.source_payment_id:
                raise ValidationError("La solicitud de reembolso no corresponde al pago origen.")
        if self.amount is not None and self.amount <= 0:
            raise ValidationError("El saldo a favor debe ser mayor a cero.")
        if self.remaining_amount is not None and self.remaining_amount < 0:
            raise ValidationError("El saldo disponible no puede ser negativo.")
        if (
            self.amount is not None
            and self.remaining_amount is not None
            and self.remaining_amount > self.amount
        ):
            raise ValidationError("El saldo disponible no puede superar el monto emitido.")

    def __str__(self):
        return (
            f"Crédito cliente {self.customer_id} · ${self.remaining_amount} "
            f"de ${self.amount}"
        )

class CustomerCreditApplication(models.Model):
    """Aplicación auditable de un saldo a favor a un pedido posterior.

    No es un cobro y nunca debe representarse como Payment. Una aplicación puede
    revertirse (por ejemplo, durante una cancelación comercial) devolviendo el
    importe al CustomerCredit origen.
    """

    STATUS_APPLIED = "applied"
    STATUS_REVERSED = "reversed"
    STATUS_CHOICES = [
        (STATUS_APPLIED, "Aplicado"),
        (STATUS_REVERSED, "Revertido"),
    ]

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="customer_credit_applications"
    )
    customer = models.ForeignKey(
        "crm.Customer", on_delete=models.PROTECT, related_name="credit_applications"
    )
    credit = models.ForeignKey(
        CustomerCredit, on_delete=models.PROTECT, related_name="applications"
    )
    order = models.ForeignKey(
        Order, on_delete=models.PROTECT, related_name="customer_credit_applications"
    )
    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="applied_customer_credits",
    )
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reversed_customer_credits",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_APPLIED, db_index=True
    )
    reason = models.TextField()
    reversal_reason = models.TextField(blank=True)
    idempotency_key = models.CharField(
        max_length=64, db_index=True, null=True, blank=True
    )
    metadata = models.JSONField(default=dict, blank=True)
    applied_at = models.DateTimeField(auto_now_add=True, db_index=True)
    reversed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-applied_at", "-id"]
        indexes = [
            models.Index(
                fields=["company", "order", "status"],
                name="cred_app_comp_order_idx",
            ),
            models.Index(
                fields=["company", "credit", "status"],
                name="cred_app_comp_cred_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="cust_credit_app_amount_positive",
            ),
            models.UniqueConstraint(
                fields=["company", "idempotency_key"],
                name="uniq_credit_app_company_key",
            ),
        ]

    def clean(self):
        if self.company_id and self.customer_id and self.customer.company_id != self.company_id:
            raise ValidationError("La aplicación y el cliente pertenecen a empresas distintas.")
        if self.company_id and self.credit_id and self.credit.company_id != self.company_id:
            raise ValidationError("La aplicación y el saldo a favor pertenecen a empresas distintas.")
        if self.company_id and self.order_id and self.order.company_id != self.company_id:
            raise ValidationError("La aplicación y el pedido pertenecen a empresas distintas.")
        if self.credit_id and self.customer_id and self.credit.customer_id != self.customer_id:
            raise ValidationError("El saldo a favor pertenece a otro cliente.")
        if self.order_id and self.customer_id and self.order.customer_id != self.customer_id:
            raise ValidationError("El pedido pertenece a otro cliente.")
        if self.amount is not None and self.amount <= 0:
            raise ValidationError("El monto aplicado debe ser mayor a cero.")

    def __str__(self):
        return f"Crédito #{self.credit_id} → pedido {self.order_id} · ${self.amount}"
