from django.contrib import admin
from .models import Plan, Subscription, SubscriptionPayment
from companies.scoping import CompanyScopedAdmin


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "price",
        "billing_period",
        "max_users",
        "max_products",
        "max_warehouses",
        "has_whatsapp",
        "has_ai",
        "has_api",
        "is_active",
    )
    search_fields = ("name", "slug")
    list_filter = ("billing_period", "has_whatsapp", "has_ai", "has_api", "is_active")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Subscription)
class SubscriptionAdmin(CompanyScopedAdmin):    
    list_display = (
        "company",
        "plan",
        "status",
        "start_date",
        "end_date",
        "next_billing_date",
        "trial_ends_at",
        "auto_renew",
        "is_valid",
    )
    search_fields = (
        "company__name",
        "plan__name",
        "stripe_customer_id",
        "stripe_subscription_id",
    )
    list_filter = ("status", "plan", "auto_renew")


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(CompanyScopedAdmin):    
    list_display = (
        "folio",
        "company",
        "subscription",
        "amount",
        "payment_method",
        "status",
        "paid_at",
        "created_at",
    )
    search_fields = ("folio", "company__name", "transaction_id")
    list_filter = ("payment_method", "status", "created_at")
