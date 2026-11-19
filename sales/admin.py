from django.contrib import admin
from django.contrib.admin.sites import NotRegistered

from sales.models import Quote, QuoteItem, Order, OrderItem
from companies.scoping import CompanyScopedAdmin

for model in [Quote, QuoteItem, Order, OrderItem]:
    try:
        admin.site.unregister(model)
    except NotRegistered:
        pass


class QuoteItemInline(admin.TabularInline):
    model = QuoteItem
    extra = 0

    fields = [
        "variant",
        "quantity",
        "unit_price",
        "total",
    ]

    readonly_fields = [
        "total",
    ]

    autocomplete_fields = [
        "variant",
    ]


@admin.register(Quote)
class QuoteAdmin(CompanyScopedAdmin):
    list_display = [
        "folio",
        "customer",
        "company",
        "status",
        "subtotal",
        "discount",
        "tax",
        "total",
        "created_at",
    ]

    list_filter = [
        "company",
        "status",
        "created_at",
    ]

    search_fields = [
        "folio",
        "customer__full_name",
        "customer__business_name",
    ]

    readonly_fields = [
        "folio",
        "subtotal",
        "total",
        "created_at",
        "updated_at",
    ]

    autocomplete_fields = [
        "customer",
    ]

    inlines = [
        QuoteItemInline,
    ]


@admin.register(QuoteItem)
class QuoteItemAdmin(CompanyScopedAdmin):
    list_display = [
        "quote",
        "variant",
        "quantity",
        "unit_price",
        "total",
    ]

    search_fields = [
        "quote__folio",
        "variant__sku",
        "variant__product__name",
    ]

    readonly_fields = [
        "total",
    ]

    autocomplete_fields = [
        "quote",
        "variant",
    ]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0

    fields = [
        "variant",
        "quantity",
        "unit_price",
        "total",
        "unit_cost",
        "total_cost",
        "gross_profit",
        "margin_percent",
    ]

    readonly_fields = [
        "total",
        "total_cost",
        "gross_profit",
        "margin_percent",
    ]

    autocomplete_fields = [
        "variant",
    ]


@admin.register(Order)
class OrderAdmin(CompanyScopedAdmin):

    list_display = [
        "folio",
        "customer",
        "company",
        "status",
        "total",
        "amount_paid",
        "balance_due",
        "total_cost",
        "gross_profit",
        "margin_percent",
        "created_at",
    ]

    list_filter = [
        "company",
        "status",
        "created_at",
    ]

    search_fields = [
        "folio",
        "customer__full_name",
        "customer__business_name",
    ]

    readonly_fields = [
        "folio",
        "subtotal",
        "total",
        "balance_due",
        "total_cost",
        "gross_profit",
        "margin_percent",
        "created_at",
        "updated_at",
    ]

    autocomplete_fields = [
        "customer",
        "quote",
    ]

    inlines = [
        OrderItemInline,
    ]

    fieldsets = (
        (
            "Pedido",
            {
                "fields": (
                    "company",
                    "folio",
                    "customer",
                    "quote",
                    "created_by",
                    "status",
                )
            },
        ),
        (
            "Totales de venta",
            {
                "fields": (
                    "subtotal",
                    "discount",
                    "tax",
                    "shipping_cost",
                    "total",
                )
            },
        ),
        (
            "Pagos",
            {
                "fields": (
                    "amount_paid",
                    "balance_due",
                )
            },
        ),
        (
            "Rentabilidad",
            {
                "fields": (
                    "total_cost",
                    "gross_profit",
                    "margin_percent",
                )
            },
        ),
        (
            "Notas",
            {"fields": ("notes",)},
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


@admin.register(OrderItem)
class OrderItemAdmin(CompanyScopedAdmin):
    list_display = [
        "order",
        "variant",
        "quantity",
        "unit_price",
        "total",
        "unit_cost",
        "total_cost",
        "gross_profit",
        "margin_percent",
    ]

    list_filter = [
        "order__company",
        "order__status",
    ]

    search_fields = [
        "order__folio",
        "variant__sku",
        "variant__product__name",
    ]

    readonly_fields = [
        "total",
        "total_cost",
        "gross_profit",
        "margin_percent",
    ]

    autocomplete_fields = [
        "order",
        "variant",
    ]
