from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from accounts.models import UserProfile
from crm.models import Customer
from inventory.models import Product, Warehouse
from messaging.models import WhatsAppNumber
from sales.models import Order
from subscriptions.models import SubscriptionPayment

from .media_models import (
    CompanyBranchPhoto,
    CompanyBranding,
    CompanyDocument,
)


ZERO = Decimal("0.00")


def percentage(current, maximum):
    try:
        current_value = int(current or 0)
        maximum_value = int(maximum or 0)
    except (TypeError, ValueError):
        return 0

    if maximum_value <= 0:
        return 0

    return min(
        100,
        round((current_value / maximum_value) * 100),
    )


def get_company_subscription(company):
    try:
        return company.subscription
    except Exception:
        return None


def get_company_profile_context(company):
    now = timezone.now()
    today = timezone.localdate()
    month_start = now.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    users = UserProfile.objects.filter(
        company=company,
    ).select_related("user")

    active_users = users.filter(
        is_active=True,
        user__is_active=True,
    )

    customers = Customer.objects.filter(
        company=company,
    )

    products = Product.objects.filter(
        company=company,
    )

    warehouses = Warehouse.objects.filter(
        company=company,
    )

    orders = Order.objects.filter(
        company=company,
    )

    commercial_orders = orders.exclude(
        status__in=[
            "cancelled",
            "draft",
        ],
    )

    month_orders = commercial_orders.filter(
        created_at__gte=month_start,
    )

    month_totals = month_orders.aggregate(
        sales=Sum("total"),
        profit=Sum("gross_profit"),
        pending=Sum("balance_due"),
    )

    total_totals = commercial_orders.aggregate(
        sales=Sum("total"),
        profit=Sum("gross_profit"),
        pending=Sum("balance_due"),
    )

    subscription = get_company_subscription(
        company
    )

    plan = (
        subscription.plan
        if subscription
        else None
    )

    renewal_date = None

    if subscription:
        renewal_date = (
            subscription.next_billing_date
            or subscription.end_date
            or subscription.trial_ends_at
        )

    days_to_renewal = None

    if renewal_date:
        days_to_renewal = (
            renewal_date - today
        ).days

    subscription_payments = (
        SubscriptionPayment.objects.filter(
            company=company,
        )
        .select_related(
            "subscription",
        )
        .order_by(
            "-created_at",
        )[:8]
    )

    channels = WhatsAppNumber.objects.filter(
        company=company,
    )

    whatsapp_credits = (
        channels.aggregate(
            total=Sum(
                "creditos_disponibles"
            )
        )["total"]
        or 0
    )

    users_count = users.count()
    active_users_count = active_users.count()
    customers_count = customers.count()
    products_count = products.count()
    active_products_count = products.filter(
        is_active=True,
    ).count()
    warehouses_count = warehouses.count()
    active_warehouses_count = warehouses.filter(
        is_active=True,
    ).count()

    orders_count = commercial_orders.count()
    month_orders_count = month_orders.count()
    delivered_orders_count = commercial_orders.filter(
        status="delivered",
    ).count()
    active_orders_count = commercial_orders.exclude(
        status__in=[
            "draft",
            "delivered",
        ],
    ).count()

    usage = {
        "users": {
            "current": active_users_count,
            "maximum": getattr(
                plan,
                "max_users",
                0,
            ),
        },
        "products": {
            "current": products_count,
            "maximum": getattr(
                plan,
                "max_products",
                0,
            ),
        },
        "warehouses": {
            "current": active_warehouses_count,
            "maximum": getattr(
                plan,
                "max_warehouses",
                0,
            ),
        },
    }

    for item in usage.values():
        item["percentage"] = percentage(
            item["current"],
            item["maximum"],
        )

    health_score = 0

    if company.is_active:
        health_score += 20

    if subscription and subscription.is_valid:
        health_score += 35

    if active_users_count:
        health_score += 10

    if month_orders_count:
        health_score += 20

    if active_products_count:
        health_score += 10

    if active_warehouses_count:
        health_score += 5

    if health_score >= 85:
        health_label = "Excelente"
        health_class = "success"
    elif health_score >= 65:
        health_label = "Saludable"
        health_class = "info"
    elif health_score >= 40:
        health_label = "Requiere atención"
        health_class = "warning"
    else:
        health_label = "En riesgo"
        health_class = "danger"

    company_lines = (
        company.company_business_lines
        .select_related(
            "business_line",
        )
        .order_by(
            "-is_primary",
            "business_line__name",
        )
    )

    recent_orders = (
        commercial_orders
        .select_related(
            "customer",
        )
        .order_by(
            "-created_at",
        )[:8]
    )

    recent_users = (
        users
        .select_related(
            "user",
        )
        .order_by(
            "-user__last_login",
            "-created_at",
        )[:8]
    )

    recent_warehouses = (
        warehouses
        .order_by(
            "-is_active",
            "name",
        )[:8]
    )

    name_words = [
        word
        for word in company.name.split()
        if word
    ]

    if not name_words:
        initials = "EM"
    elif len(name_words) == 1:
        initials = name_words[0][:2].upper()
    else:
        initials = (
            name_words[0][0]
            + name_words[-1][0]
        ).upper()

    avatar_gradient_index = sum(
        (index + 1) * ord(character)
        for index, character in enumerate(company.name.lower())
    ) % 8

    branding = CompanyBranding.objects.filter(
        company=company,
    ).first()

    branch_photos = (
        CompanyBranchPhoto.objects.filter(
            company=company,
        )
        .select_related(
            "warehouse",
            "uploaded_by",
        )
        .order_by(
            "-created_at",
        )
    )

    company_documents = (
        CompanyDocument.objects.filter(
            company=company,
        )
        .select_related(
            "uploaded_by",
        )
        .order_by(
            "-created_at",
        )
    )

    return {
        "company": company,
        "initials": initials,
        "avatar_gradient_index": avatar_gradient_index,
        "branding": branding,
        "branch_photos": branch_photos,
        "company_documents": company_documents,
        "company_lines": company_lines,
        "subscription": subscription,
        "plan": plan,
        "renewal_date": renewal_date,
        "days_to_renewal": days_to_renewal,
        "subscription_payments": subscription_payments,
        "users_count": users_count,
        "active_users_count": active_users_count,
        "customers_count": customers_count,
        "products_count": products_count,
        "active_products_count": active_products_count,
        "warehouses_count": warehouses_count,
        "active_warehouses_count": active_warehouses_count,
        "orders_count": orders_count,
        "month_orders_count": month_orders_count,
        "delivered_orders_count": delivered_orders_count,
        "active_orders_count": active_orders_count,
        "month_sales": month_totals["sales"] or ZERO,
        "month_profit": month_totals["profit"] or ZERO,
        "month_pending": month_totals["pending"] or ZERO,
        "total_sales": total_totals["sales"] or ZERO,
        "total_profit": total_totals["profit"] or ZERO,
        "total_pending": total_totals["pending"] or ZERO,
        "channels_count": channels.count(),
        "whatsapp_credits": whatsapp_credits,
        "usage": usage,
        "health_score": health_score,
        "health_label": health_label,
        "health_class": health_class,
        "recent_orders": recent_orders,
        "recent_users": recent_users,
        "recent_warehouses": recent_warehouses,
    }
