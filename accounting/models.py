from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone

from companies.models import Company


class AccountingAccount(models.Model):
    TYPE_ASSET = "asset"
    TYPE_LIABILITY = "liability"
    TYPE_EQUITY = "equity"
    TYPE_INCOME = "income"
    TYPE_COST = "cost"
    TYPE_EXPENSE = "expense"

    ACCOUNT_TYPE_CHOICES = [
        (TYPE_ASSET, "Activo"),
        (TYPE_LIABILITY, "Pasivo"),
        (TYPE_EQUITY, "Capital"),
        (TYPE_INCOME, "Ingreso"),
        (TYPE_COST, "Costo"),
        (TYPE_EXPENSE, "Gasto"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="accounting_accounts",
    )

    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        blank=True,
        null=True,
    )

    code = models.CharField(
        "Código",
        max_length=30,
    )

    name = models.CharField(
        "Nombre",
        max_length=180,
    )

    account_type = models.CharField(
        "Tipo de cuenta",
        max_length=20,
        choices=ACCOUNT_TYPE_CHOICES,
    )

    description = models.TextField(
        "Descripción",
        blank=True,
    )

    is_system = models.BooleanField(
        "Cuenta del sistema",
        default=False,
    )

    is_active = models.BooleanField(
        "Activa",
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
        verbose_name = "Cuenta contable"
        verbose_name_plural = "Cuentas contables"
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"],
                name="unique_accounting_code_per_company",
            ),
        ]

    def clean(self):
        errors = {}

        if self.parent_id:
            if self.parent_id == self.pk:
                errors["parent"] = "Una cuenta no puede ser su propia cuenta padre."

            if self.company_id and self.parent.company_id != self.company_id:
                errors["parent"] = "La cuenta padre no pertenece a la misma empresa."

        if errors:
            raise ValidationError(errors)

    @property
    def normal_balance(self):
        if self.account_type in [
            self.TYPE_ASSET,
            self.TYPE_COST,
            self.TYPE_EXPENSE,
        ]:
            return "debit"

        return "credit"

    def balance_from_totals(self, debit_total, credit_total):
        debit_total = Decimal(str(debit_total or 0))
        credit_total = Decimal(str(credit_total or 0))

        if self.normal_balance == "debit":
            return debit_total - credit_total

        return credit_total - debit_total

    def __str__(self):
        return f"{self.code} - {self.name}"


class JournalEntry(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_POSTED = "posted"
    STATUS_VOID = "void"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Borrador"),
        (STATUS_POSTED, "Contabilizada"),
        (STATUS_VOID, "Anulada"),
    ]

    SOURCE_MANUAL = "manual"
    SOURCE_OPENING = "opening_balance"
    SOURCE_SALE = "sale"
    SOURCE_COST_OF_SALE = "cost_of_sale"
    SOURCE_CUSTOMER_PAYMENT = "customer_payment"
    SOURCE_PURCHASE = "purchase"
    SOURCE_SUPPLIER_PAYMENT = "supplier_payment"
    SOURCE_EXPENSE = "expense"
    SOURCE_ADJUSTMENT = "adjustment"
    SOURCE_REVERSAL = "reversal"

    SOURCE_CHOICES = [
        (SOURCE_MANUAL, "Manual"),
        (SOURCE_OPENING, "Saldo inicial"),
        (SOURCE_SALE, "Venta"),
        (SOURCE_COST_OF_SALE, "Costo de venta"),
        (SOURCE_CUSTOMER_PAYMENT, "Pago de cliente"),
        (SOURCE_PURCHASE, "Compra"),
        (SOURCE_SUPPLIER_PAYMENT, "Pago a proveedor"),
        (SOURCE_EXPENSE, "Gasto"),
        (SOURCE_ADJUSTMENT, "Ajuste"),
        (SOURCE_REVERSAL, "Reversión"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="journal_entries",
    )

    entry_date = models.DateField(
        "Fecha contable",
        default=timezone.localdate,
        db_index=True,
    )

    source = models.CharField(
        "Origen",
        max_length=40,
        choices=SOURCE_CHOICES,
        default=SOURCE_MANUAL,
        db_index=True,
    )

    description = models.CharField(
        "Descripción",
        max_length=255,
    )

    reference = models.CharField(
        "Referencia",
        max_length=120,
        blank=True,
    )

    external_model = models.CharField(
        "Modelo origen",
        max_length=120,
        blank=True,
    )

    external_id = models.PositiveBigIntegerField(
        "ID origen",
        blank=True,
        null=True,
    )

    idempotency_key = models.CharField(
        "Clave de idempotencia",
        max_length=180,
        blank=True,
        null=True,
    )

    status = models.CharField(
        "Estado",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_journal_entries",
        blank=True,
        null=True,
    )

    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="posted_journal_entries",
        blank=True,
        null=True,
    )

    posted_at = models.DateTimeField(
        "Fecha de contabilización",
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
        verbose_name = "Póliza contable"
        verbose_name_plural = "Pólizas contables"
        ordering = ["-entry_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "idempotency_key"],
                name="unique_accounting_idempotency_per_company",
            ),
        ]

    @property
    def debit_total(self):
        return self.lines.aggregate(total=Sum("debit"))["total"] or Decimal("0.00")

    @property
    def credit_total(self):
        return self.lines.aggregate(total=Sum("credit"))["total"] or Decimal("0.00")

    @property
    def difference(self):
        return self.debit_total - self.credit_total

    @property
    def is_balanced(self):
        return self.debit_total == self.credit_total

    @transaction.atomic
    def post(self, user=None):
        locked_entry = JournalEntry.objects.select_for_update().get(pk=self.pk)

        if locked_entry.status == self.STATUS_POSTED:
            return locked_entry

        if locked_entry.status == self.STATUS_VOID:
            raise ValidationError("Una póliza anulada no puede contabilizarse.")

        if not locked_entry.lines.exists():
            raise ValidationError("La póliza debe contener al menos una línea.")

        debit_total = locked_entry.debit_total
        credit_total = locked_entry.credit_total

        if debit_total <= 0:
            raise ValidationError("La póliza debe tener movimientos mayores a cero.")

        if debit_total != credit_total:
            raise ValidationError(
                (
                    "La póliza no está cuadrada. "
                    f"Cargos: ${debit_total:,.2f}. "
                    f"Abonos: ${credit_total:,.2f}."
                )
            )

        locked_entry.status = self.STATUS_POSTED
        locked_entry.posted_at = timezone.now()

        if user and getattr(user, "is_authenticated", False):
            locked_entry.posted_by = user

        locked_entry.save(
            update_fields=[
                "status",
                "posted_at",
                "posted_by",
                "updated_at",
            ]
        )

        self.status = locked_entry.status
        self.posted_at = locked_entry.posted_at
        self.posted_by = locked_entry.posted_by

        return locked_entry

    def __str__(self):
        return (
            f"{self.entry_date} - "
            f"{self.description} - "
            f"{self.get_status_display()}"
        )

    def save(self, *args, **kwargs):
        if self.pk:
            previous_status = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
            )
            if previous_status == self.STATUS_POSTED:
                raise ValidationError(
                    "Esta póliza ya fue contabilizada y es inmutable; "
                    "crea una póliza de reversión (SOURCE_REVERSAL)."
                )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status == self.STATUS_POSTED:
            raise ValidationError(
                "Las pólizas contabilizadas son auditables y no se eliminan; "
                "usa una póliza de reversión."
            )
        return super().delete(*args, **kwargs)


class JournalLine(models.Model):
    entry = models.ForeignKey(
        JournalEntry,
        on_delete=models.CASCADE,
        related_name="lines",
    )

    account = models.ForeignKey(
        AccountingAccount,
        on_delete=models.PROTECT,
        related_name="journal_lines",
    )

    description = models.CharField(
        "Descripción",
        max_length=255,
        blank=True,
    )

    debit = models.DecimalField(
        "Cargo",
        max_digits=16,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    credit = models.DecimalField(
        "Abono",
        max_digits=16,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    created_at = models.DateTimeField(
        "Fecha de creación",
        auto_now_add=True,
    )

    class Meta:
        verbose_name = "Línea contable"
        verbose_name_plural = "Líneas contables"
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(debit__gte=0),
                name="journal_line_debit_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(credit__gte=0),
                name="journal_line_credit_non_negative",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        debit__gt=0,
                        credit=Decimal("0.00"),
                    )
                    | models.Q(
                        credit__gt=0,
                        debit=Decimal("0.00"),
                    )
                ),
                name="journal_line_only_one_side",
            ),
        ]

    def clean(self):
        errors = {}

        debit = Decimal(str(self.debit or 0))
        credit = Decimal(str(self.credit or 0))

        if self.entry_id and self.account_id:
            if self.entry.company_id != self.account.company_id:
                errors["account"] = "La cuenta contable pertenece a otra empresa."

        if debit < 0:
            errors["debit"] = "El cargo no puede ser negativo."

        if credit < 0:
            errors["credit"] = "El abono no puede ser negativo."

        if debit > 0 and credit > 0:
            errors["credit"] = "Una línea no puede tener cargo y abono al mismo tiempo."

        if debit <= 0 and credit <= 0:
            errors["debit"] = "La línea debe tener un cargo o un abono mayor a cero."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return (
            f"{self.entry.entry_date} - "
            f"{self.account.code} - "
            f"D {self.debit} / C {self.credit}"
        )


class ExpenseCategory(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="expense_categories",
    )

    name = models.CharField(
        "Nombre",
        max_length=150,
    )

    expense_account = models.ForeignKey(
        AccountingAccount,
        on_delete=models.PROTECT,
        related_name="expense_categories",
    )

    is_active = models.BooleanField(
        "Activa",
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
        verbose_name = "Categoría de gasto"
        verbose_name_plural = "Categorías de gasto"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"],
                name="unique_expense_category_per_company",
            ),
        ]

    def clean(self):
        errors = {}

        if self.expense_account_id:
            if self.company_id and self.expense_account.company_id != self.company_id:
                errors["expense_account"] = (
                    "La cuenta contable pertenece a otra empresa."
                )

            if self.expense_account.account_type != AccountingAccount.TYPE_EXPENSE:
                errors["expense_account"] = (
                    "La cuenta seleccionada debe ser de tipo Gasto."
                )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.name


class Expense(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_POSTED = "posted"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Borrador"),
        (STATUS_POSTED, "Contabilizado"),
        (STATUS_CANCELLED, "Cancelado"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="expenses",
    )

    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.PROTECT,
        related_name="expenses",
    )

    payment_account = models.ForeignKey(
        AccountingAccount,
        on_delete=models.PROTECT,
        related_name="paid_expenses",
        help_text="Cuenta de Caja o Bancos desde donde salió el dinero.",
    )

    expense_date = models.DateField(
        "Fecha del gasto",
        default=timezone.localdate,
        db_index=True,
    )

    description = models.CharField(
        "Concepto",
        max_length=255,
    )

    supplier_name = models.CharField(
        "Proveedor o beneficiario",
        max_length=180,
        blank=True,
    )

    amount = models.DecimalField(
        "Importe",
        max_digits=16,
        decimal_places=2,
    )

    reference = models.CharField(
        "Referencia",
        max_length=160,
        blank=True,
    )

    notes = models.TextField(
        "Notas",
        blank=True,
    )

    receipt_file = models.FileField(
        "Comprobante",
        upload_to="accounting/expenses/",
        blank=True,
        null=True,
    )

    status = models.CharField(
        "Estado",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )

    journal_entry = models.OneToOneField(
        JournalEntry,
        on_delete=models.PROTECT,
        related_name="expense",
        blank=True,
        null=True,
    )

    reversal_entry = models.OneToOneField(
        JournalEntry,
        on_delete=models.PROTECT,
        related_name="reversed_expense",
        blank=True,
        null=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_expenses",
        blank=True,
        null=True,
    )

    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="posted_expenses",
        blank=True,
        null=True,
    )

    posted_at = models.DateTimeField(
        "Fecha de contabilización",
        blank=True,
        null=True,
    )

    cancelled_at = models.DateTimeField(
        "Fecha de cancelación",
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
        verbose_name = "Gasto"
        verbose_name_plural = "Gastos"
        ordering = ["-expense_date", "-id"]

    def clean(self):
        errors = {}

        if self.amount is not None and self.amount <= 0:
            errors["amount"] = "El importe debe ser mayor a cero."

        if self.category_id:
            if self.company_id and self.category.company_id != self.company_id:
                errors["category"] = "La categoría pertenece a otra empresa."

        if self.payment_account_id:
            if self.company_id and self.payment_account.company_id != self.company_id:
                errors["payment_account"] = (
                    "La cuenta de pago pertenece a otra empresa."
                )

            if self.payment_account.account_type != AccountingAccount.TYPE_ASSET:
                errors["payment_account"] = (
                    "La cuenta de pago debe ser de tipo Activo, "
                    "por ejemplo Caja o Bancos."
                )

        if errors:
            raise ValidationError(errors)

    @transaction.atomic
    def post(self, user=None):
        if not self.pk:
            self.full_clean()
            self.save()

        locked_expense = (
            Expense.objects.select_for_update()
            .select_related(
                "category",
                "category__expense_account",
                "payment_account",
            )
            .get(pk=self.pk)
        )

        if locked_expense.status == self.STATUS_POSTED:
            return locked_expense

        if locked_expense.status == self.STATUS_CANCELLED:
            raise ValidationError("Un gasto cancelado no puede contabilizarse.")

        locked_expense.full_clean()

        entry, created = JournalEntry.objects.get_or_create(
            company=locked_expense.company,
            idempotency_key=f"expense:{locked_expense.pk}",
            defaults={
                "entry_date": locked_expense.expense_date,
                "source": JournalEntry.SOURCE_EXPENSE,
                "description": locked_expense.description,
                "reference": (locked_expense.reference or f"GASTO-{locked_expense.pk}"),
                "external_model": "accounting.Expense",
                "external_id": locked_expense.pk,
                "created_by": (
                    user if user and getattr(user, "is_authenticated", False) else None
                ),
            },
        )

        if created:
            JournalLine.objects.create(
                entry=entry,
                account=locked_expense.category.expense_account,
                description=locked_expense.description,
                debit=locked_expense.amount,
                credit=Decimal("0.00"),
            )

            JournalLine.objects.create(
                entry=entry,
                account=locked_expense.payment_account,
                description=locked_expense.description,
                debit=Decimal("0.00"),
                credit=locked_expense.amount,
            )

        entry.post(user=user)

        locked_expense.status = self.STATUS_POSTED
        locked_expense.journal_entry = entry
        locked_expense.posted_at = timezone.now()

        if user and getattr(user, "is_authenticated", False):
            locked_expense.posted_by = user

        locked_expense.save(
            update_fields=[
                "status",
                "journal_entry",
                "posted_at",
                "posted_by",
                "updated_at",
            ]
        )

        self.status = locked_expense.status
        self.journal_entry = locked_expense.journal_entry
        self.posted_at = locked_expense.posted_at
        self.posted_by = locked_expense.posted_by

        return locked_expense

    @transaction.atomic
    def cancel(self, user=None):
        locked_expense = (
            Expense.objects.select_for_update()
            .select_related("journal_entry")
            .get(pk=self.pk)
        )

        if locked_expense.status == self.STATUS_CANCELLED:
            return locked_expense

        if locked_expense.status != self.STATUS_POSTED:
            locked_expense.status = self.STATUS_CANCELLED
            locked_expense.cancelled_at = timezone.now()
            locked_expense.save(
                update_fields=[
                    "status",
                    "cancelled_at",
                    "updated_at",
                ]
            )

            return locked_expense

        original_entry = locked_expense.journal_entry

        if not original_entry:
            raise ValidationError("El gasto contabilizado no tiene póliza relacionada.")

        reversal, created = JournalEntry.objects.get_or_create(
            company=locked_expense.company,
            idempotency_key=f"expense-reversal:{locked_expense.pk}",
            defaults={
                "entry_date": timezone.localdate(),
                "source": JournalEntry.SOURCE_REVERSAL,
                "description": (f"Reversión de gasto: {locked_expense.description}"),
                "reference": (locked_expense.reference or f"GASTO-{locked_expense.pk}"),
                "external_model": "accounting.Expense",
                "external_id": locked_expense.pk,
                "created_by": (
                    user if user and getattr(user, "is_authenticated", False) else None
                ),
            },
        )

        if created:
            for original_line in original_entry.lines.select_related("account"):
                JournalLine.objects.create(
                    entry=reversal,
                    account=original_line.account,
                    description=(f"Reversión: {original_line.description}"),
                    debit=original_line.credit,
                    credit=original_line.debit,
                )

        reversal.post(user=user)

        locked_expense.status = self.STATUS_CANCELLED
        locked_expense.reversal_entry = reversal
        locked_expense.cancelled_at = timezone.now()

        locked_expense.save(
            update_fields=[
                "status",
                "reversal_entry",
                "cancelled_at",
                "updated_at",
            ]
        )

        self.status = locked_expense.status
        self.reversal_entry = locked_expense.reversal_entry
        self.cancelled_at = locked_expense.cancelled_at

        return locked_expense

    def __str__(self):
        return f"{self.expense_date} - " f"{self.description} - " f"${self.amount}"
