from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from companies.scoping import CompanyScopedAdmin
from purchases.models import (
    Supplier,
    PurchaseOrder,
    PurchaseOrderItem,
    GoodsReceipt,
    GoodsReceiptItem,
    SupplierPayment,
)
from purchases.services import (
    mark_purchase_order_sent,
    cancel_purchase_order,
    create_goods_receipt_from_purchase_order,
    confirm_goods_receipt,
    cancel_goods_receipt,
    approve_supplier_payment,
    reject_supplier_payment,
    cancel_supplier_payment,
)


@admin.register(Supplier)
class SupplierAdmin(CompanyScopedAdmin):
    list_display = [
        "name",
        "company",
        "contact_name",
        "phone",
        "email",
        "payment_terms",
        "credit_days",
        "is_active",
    ]

    list_filter = [
        "company",
        "is_active",
        "country",
        "state",
    ]

    search_fields = [
        "name",
        "legal_name",
        "rfc",
        "contact_name",
        "email",
        "phone",
        "whatsapp",
    ]

    readonly_fields = [
        "created_at",
        "updated_at",
    ]

    fieldsets = (
        (
            "Empresa",
            {
                "fields": (
                    "company",
                    "is_active",
                )
            },
        ),
        (
            "Datos del proveedor",
            {
                "fields": (
                    "name",
                    "legal_name",
                    "rfc",
                )
            },
        ),
        (
            "Contacto",
            {
                "fields": (
                    "contact_name",
                    "email",
                    "phone",
                    "whatsapp",
                )
            },
        ),
        (
            "Dirección",
            {
                "fields": (
                    "address",
                    "city",
                    "state",
                    "country",
                )
            },
        ),
        (
            "Condiciones comerciales",
            {
                "fields": (
                    "payment_terms",
                    "credit_days",
                    "notes",
                )
            },
        ),
        (
            "Auditoría",
            {
                "fields": (
                    "created_by",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user

        super().save_model(request, obj, form, change)


class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 1

    fields = [
        "variant",
        "quantity_ordered",
        "quantity_received",
        "unit_cost",
        "line_total",
        "notes",
    ]

    readonly_fields = [
        "quantity_received",
        "line_total",
    ]

    autocomplete_fields = [
        "variant",
    ]


class SupplierPaymentInline(admin.TabularInline):
    model = SupplierPayment
    extra = 0

    fields = [
        "folio",
        "amount",
        "method",
        "status",
        "payment_date",
        "reference",
        "registered_by",
        "reviewed_by",
        "reviewed_at",
    ]

    readonly_fields = [
        "folio",
        "status",
        "payment_date",
        "registered_by",
        "reviewed_by",
        "reviewed_at",
    ]

    can_delete = False


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(CompanyScopedAdmin):
    list_display = [
        "folio",
        "supplier",
        "company",
        "warehouse",
        "status",
        "payment_status",
        "ordered_quantity_total",
        "received_quantity_total",
        "pending_quantity_total",
        "total",
        "created_at",
    ]

    list_filter = [
        "company",
        "warehouse",
        "supplier",
        "status",
        "payment_status",
        "created_at",
        "expected_date",
    ]

    search_fields = [
        "folio",
        "supplier__name",
        "supplier__legal_name",
        "supplier__rfc",
        "notes",
    ]

    readonly_fields = [
        "folio",
        "subtotal",
        "total",
        "ordered_quantity_total",
        "received_quantity_total",
        "pending_quantity_total",
        "ordered_at",
        "received_at",
        "cancelled_at",
        "created_at",
        "updated_at",
    ]

    autocomplete_fields = [
        "supplier",
        "warehouse",
    ]

    inlines = [
        PurchaseOrderItemInline,
        SupplierPaymentInline,
    ]

    actions = [
        "admin_mark_sent",
        "admin_create_receipt",
        "admin_cancel_purchase_order",
    ]

    fieldsets = (
        (
            "Orden de compra",
            {
                "fields": (
                    "company",
                    "folio",
                    "supplier",
                    "warehouse",
                    "status",
                    "payment_status",
                    "expected_date",
                )
            },
        ),
        (
            "Totales",
            {
                "fields": (
                    "subtotal",
                    "tax",
                    "shipping_cost",
                    "discount",
                    "total",
                )
            },
        ),
        (
            "Recepción",
            {
                "fields": (
                    "ordered_quantity_total",
                    "received_quantity_total",
                    "pending_quantity_total",
                    "ordered_at",
                    "received_at",
                )
            },
        ),
        (
            "Notas",
            {"fields": ("notes",)},
        ),
        (
            "Cancelación",
            {
                "fields": (
                    "cancelled_by",
                    "cancelled_at",
                    "cancel_reason",
                )
            },
        ),
        (
            "Auditoría",
            {
                "fields": (
                    "created_by",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user

        super().save_model(request, obj, form, change)

    @admin.action(description="Marcar orden como enviada al proveedor")
    def admin_mark_sent(self, request, queryset):
        success = 0

        for purchase_order in queryset:
            try:
                mark_purchase_order_sent(
                    purchase_order=purchase_order,
                    user=request.user,
                )
                success += 1
            except ValidationError as error:
                self.message_user(
                    request,
                    f"{purchase_order.folio}: {error}",
                    level=messages.ERROR,
                )

        if success:
            self.message_user(
                request,
                f"Órdenes enviadas correctamente: {success}",
                level=messages.SUCCESS,
            )

    @admin.action(description="Crear recepción de mercancía")
    def admin_create_receipt(self, request, queryset):
        success = 0

        for purchase_order in queryset:
            try:
                receipt = create_goods_receipt_from_purchase_order(
                    purchase_order=purchase_order,
                    user=request.user,
                )
                success += 1

                self.message_user(
                    request,
                    f"Recepción creada: {receipt.folio} para OC {purchase_order.folio}",
                    level=messages.SUCCESS,
                )
            except ValidationError as error:
                self.message_user(
                    request,
                    f"{purchase_order.folio}: {error}",
                    level=messages.ERROR,
                )

        if success:
            self.message_user(
                request,
                f"Recepciones creadas correctamente: {success}",
                level=messages.SUCCESS,
            )

    @admin.action(description="Cancelar orden de compra")
    def admin_cancel_purchase_order(self, request, queryset):
        success = 0

        for purchase_order in queryset:
            try:
                cancel_purchase_order(
                    purchase_order=purchase_order,
                    user=request.user,
                    reason="Cancelada desde Django Admin.",
                )
                success += 1
            except ValidationError as error:
                self.message_user(
                    request,
                    f"{purchase_order.folio}: {error}",
                    level=messages.ERROR,
                )

        if success:
            self.message_user(
                request,
                f"Órdenes canceladas correctamente: {success}",
                level=messages.SUCCESS,
            )


class GoodsReceiptItemInline(admin.TabularInline):
    model = GoodsReceiptItem
    extra = 0

    fields = [
        "purchase_order_item",
        "variant",
        "location",
        "quantity_received",
        "unit_cost",
        "line_total",
        "added_to_stock",
        "notes",
    ]

    readonly_fields = [
        "variant",
        "line_total",
        "added_to_stock",
    ]

    autocomplete_fields = [
        "purchase_order_item",
        "location",
    ]

    def line_total(self, obj):
        if not obj.pk:
            return "$0.00"

        return f"${obj.line_total:.2f}"

    line_total.short_description = "Total"


@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(CompanyScopedAdmin):
    list_display = [
        "folio",
        "purchase_order",
        "company",
        "warehouse",
        "status",
        "received_by",
        "received_at",
        "created_at",
    ]

    list_filter = [
        "company",
        "warehouse",
        "status",
        "received_at",
        "created_at",
    ]

    search_fields = [
        "folio",
        "purchase_order__folio",
        "purchase_order__supplier__name",
        "notes",
    ]

    readonly_fields = [
        "folio",
        "received_by",
        "received_at",
        "cancelled_by",
        "cancelled_at",
        "created_at",
        "updated_at",
    ]

    autocomplete_fields = [
        "purchase_order",
        "warehouse",
    ]

    inlines = [
        GoodsReceiptItemInline,
    ]

    actions = [
        "admin_confirm_receipt",
        "admin_cancel_receipt",
    ]

    fieldsets = (
        (
            "Recepción",
            {
                "fields": (
                    "company",
                    "folio",
                    "purchase_order",
                    "warehouse",
                    "status",
                    "notes",
                )
            },
        ),
        (
            "Confirmación",
            {
                "fields": (
                    "received_by",
                    "received_at",
                )
            },
        ),
        (
            "Cancelación",
            {
                "fields": (
                    "cancelled_by",
                    "cancelled_at",
                    "cancel_reason",
                )
            },
        ),
        (
            "Auditoría",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    @admin.action(description="Confirmar recepción y agregar al stock")
    def admin_confirm_receipt(self, request, queryset):
        success = 0

        for receipt in queryset:
            try:
                confirm_goods_receipt(
                    receipt=receipt,
                    user=request.user,
                )
                success += 1
            except ValidationError as error:
                self.message_user(
                    request,
                    f"{receipt.folio}: {error}",
                    level=messages.ERROR,
                )

        if success:
            self.message_user(
                request,
                f"Recepciones confirmadas correctamente: {success}",
                level=messages.SUCCESS,
            )

    @admin.action(description="Cancelar recepción en borrador")
    def admin_cancel_receipt(self, request, queryset):
        success = 0

        for receipt in queryset:
            try:
                cancel_goods_receipt(
                    receipt=receipt,
                    user=request.user,
                    reason="Cancelada desde Django Admin.",
                )
                success += 1
            except ValidationError as error:
                self.message_user(
                    request,
                    f"{receipt.folio}: {error}",
                    level=messages.ERROR,
                )

        if success:
            self.message_user(
                request,
                f"Recepciones canceladas correctamente: {success}",
                level=messages.SUCCESS,
            )


@admin.register(PurchaseOrderItem)
class PurchaseOrderItemAdmin(CompanyScopedAdmin):
    list_display = [
        "purchase_order",
        "variant",
        "quantity_ordered",
        "quantity_received",
        "pending_quantity",
        "unit_cost",
        "line_total",
    ]

    list_filter = [
        "purchase_order__company",
        "purchase_order__warehouse",
        "purchase_order__status",
    ]

    search_fields = [
        "purchase_order__folio",
        "variant__sku",
        "variant__product__name",
    ]

    readonly_fields = [
        "line_total",
        "pending_quantity",
        "created_at",
        "updated_at",
    ]

    autocomplete_fields = [
        "purchase_order",
        "variant",
    ]


@admin.register(GoodsReceiptItem)
class GoodsReceiptItemAdmin(CompanyScopedAdmin):
    list_display = [
        "receipt",
        "purchase_order_item",
        "variant",
        "location",
        "quantity_received",
        "unit_cost",
        "line_total",
        "added_to_stock",
    ]

    list_filter = [
        "receipt__company",
        "receipt__warehouse",
        "receipt__status",
        "added_to_stock",
    ]

    search_fields = [
        "receipt__folio",
        "receipt__purchase_order__folio",
        "variant__sku",
        "variant__product__name",
        "location__bin_code",
    ]

    readonly_fields = [
        "line_total",
        "added_to_stock",
        "created_at",
        "updated_at",
    ]

    autocomplete_fields = [
        "receipt",
        "purchase_order_item",
        "variant",
        "location",
    ]


@admin.register(SupplierPayment)
class SupplierPaymentAdmin(CompanyScopedAdmin):
    list_display = [
        "folio",
        "purchase_order",
        "supplier",
        "company",
        "amount",
        "method",
        "status",
        "payment_date",
        "registered_by",
        "reviewed_by",
        "created_at",
    ]

    list_filter = [
        "company",
        "supplier",
        "method",
        "status",
        "payment_date",
        "created_at",
    ]

    search_fields = [
        "folio",
        "purchase_order__folio",
        "supplier__name",
        "supplier__legal_name",
        "reference",
        "notes",
    ]

    readonly_fields = [
        "folio",
        "supplier",
        "company",
        "status",
        "payment_date",
        "registered_by",
        "reviewed_by",
        "reviewed_at",
        "rejection_reason",
        "cancel_reason",
        "created_at",
        "updated_at",
    ]

    autocomplete_fields = [
        "purchase_order",
    ]

    actions = [
        "admin_approve_supplier_payment",
        "admin_reject_supplier_payment",
        "admin_cancel_supplier_payment",
    ]

    fieldsets = (
        (
            "Pago a proveedor",
            {
                "fields": (
                    "company",
                    "folio",
                    "purchase_order",
                    "supplier",
                    "amount",
                    "method",
                    "status",
                )
            },
        ),
        (
            "Referencia",
            {
                "fields": (
                    "reference",
                    "notes",
                    "receipt_file",
                )
            },
        ),
        (
            "Fechas y revisión",
            {
                "fields": (
                    "payment_date",
                    "registered_by",
                    "reviewed_by",
                    "reviewed_at",
                )
            },
        ),
        (
            "Rechazo / cancelación",
            {
                "fields": (
                    "rejection_reason",
                    "cancel_reason",
                )
            },
        ),
        (
            "Auditoría",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        if obj.purchase_order_id:
            obj.company = obj.purchase_order.company
            obj.supplier = obj.purchase_order.supplier

        if not obj.registered_by_id:
            obj.registered_by = request.user

        super().save_model(request, obj, form, change)
        obj.sync_purchase_order_payment_status()

    @admin.action(description="Aprobar pago a proveedor")
    def admin_approve_supplier_payment(self, request, queryset):
        success = 0

        for payment in queryset:
            try:
                approve_supplier_payment(
                    payment=payment,
                    user=request.user,
                )
                success += 1
            except ValidationError as error:
                self.message_user(
                    request,
                    f"{payment.folio}: {error}",
                    level=messages.ERROR,
                )

        if success:
            self.message_user(
                request,
                f"Pagos aprobados correctamente: {success}",
                level=messages.SUCCESS,
            )

    @admin.action(description="Rechazar pago a proveedor")
    def admin_reject_supplier_payment(self, request, queryset):
        success = 0

        for payment in queryset:
            try:
                reject_supplier_payment(
                    payment=payment,
                    user=request.user,
                    reason="Rechazado desde Django Admin.",
                )
                success += 1
            except ValidationError as error:
                self.message_user(
                    request,
                    f"{payment.folio}: {error}",
                    level=messages.ERROR,
                )

        if success:
            self.message_user(
                request,
                f"Pagos rechazados correctamente: {success}",
                level=messages.SUCCESS,
            )

    @admin.action(description="Cancelar pago a proveedor")
    def admin_cancel_supplier_payment(self, request, queryset):
        success = 0

        for payment in queryset:
            try:
                cancel_supplier_payment(
                    payment=payment,
                    user=request.user,
                    reason="Cancelado desde Django Admin.",
                )
                success += 1
            except ValidationError as error:
                self.message_user(
                    request,
                    f"{payment.folio}: {error}",
                    level=messages.ERROR,
                )

        if success:
            self.message_user(
                request,
                f"Pagos cancelados correctamente: {success}",
                level=messages.SUCCESS,
            )
