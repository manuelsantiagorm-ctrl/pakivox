from django.db.models import Sum
from django.shortcuts import redirect, render

from analytics.landing_content import build_landing_context

from companies.models import Company
from crm.models import Customer
from inventory.models import Product, Stock
from payments.models import Payment
from sales.models import Order
from warehouse.models import PackingOrder, PickingOrder


def landing_page(request):
    """
    Página pública principal de WMS360.

    Los visitantes ven la landing page.
    Los usuarios autenticados son enviados al Dashboard CEO.
    """
    if request.user.is_authenticated:
        return redirect("dashboard:ceo_dashboard")

    return render(request, "public/landing.html", build_landing_context())


def dashboard_home(request):
    """
    Dashboard anterior conservado temporalmente por compatibilidad.

    IMPORTANTE:
    Esta vista no debe exponerse públicamente porque actualmente
    realiza consultas globales sin aislamiento por empresa.
    """
    total_companies = Company.objects.count()
    total_customers = Customer.objects.count()
    total_orders = Order.objects.count()

    sales_total = Order.objects.aggregate(total=Sum("total"))["total"] or 0

    payments_pending = Payment.objects.filter(
        status__in=["pending", "under_review"]
    ).count()

    picking_pending = PickingOrder.objects.filter(
        status__in=["pending", "in_progress"]
    ).count()

    packing_pending = PackingOrder.objects.filter(
        status__in=["pending", "in_progress"]
    ).count()

    products_total = Product.objects.count()

    low_stock = Stock.objects.filter(physical_stock__lte=5).count()

    context = {
        "total_companies": total_companies,
        "total_customers": total_customers,
        "total_orders": total_orders,
        "sales_total": sales_total,
        "payments_pending": payments_pending,
        "picking_pending": picking_pending,
        "packing_pending": packing_pending,
        "products_total": products_total,
        "low_stock": low_stock,
    }

    return render(request, "dashboard/home.html", context)
