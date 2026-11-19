from django.contrib import admin
from django.utils import timezone
from companies.scoping import CompanyScopedAdmin
from .models import Payment


@admin.action(description="Aprobar pagos seleccionados")
def approve_payments(modeladmin, request, queryset):
    for payment in queryset:
        payment.approve(user=request.user)


@admin.action(description="Rechazar pagos seleccionados")
def reject_payments(modeladmin, request, queryset):
    for payment in queryset:
        payment.reject(user=request.user, reason="Rechazado desde panel admin")


@admin.register(Payment)
class PaymentAdmin(CompanyScopedAdmin):
    list_display = (
        "folio",
        "company",
        "order",
        "payment_method",
        "payment_type",
        "status",
        "amount",
        "paid_at",
        "reviewed_by",
        "reviewed_at",
        "created_at",
    )

    search_fields = (
        "folio",
        "order__folio",
        "company__name",
        "transaction_id",
    )

    list_filter = (
        "company",
        "payment_method",
        "payment_type",
        "status",
        "created_at",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
        "reviewed_at",
    )

    actions = [
        approve_payments,
        reject_payments,
    ]

    fieldsets = (
        (
            "Datos principales",
            {
                "fields": (
                    "company",
                    "order",
                    "folio",
                    "payment_method",
                    "payment_type",
                    "status",
                    "amount",
                )
            },
        ),
        (
            "Comprobante / Transacción",
            {
                "fields": (
                    "proof_file",
                    "transaction_id",
                    "paid_at",
                )
            },
        ),
        (
            "Revisión administrativa",
            {
                "fields": (
                    "registered_by",
                    "reviewed_by",
                    "reviewed_at",
                    "rejection_reason",
                )
            },
        ),
        ("Notas", {"fields": ("notes",)}),
        (
            "Sistema",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )
