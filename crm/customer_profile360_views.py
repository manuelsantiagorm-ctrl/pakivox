from decimal import Decimal
from urllib.parse import quote_plus

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Min, Q, Sum
from django.http import HttpResponseForbidden
from django.shortcuts import (
    get_object_or_404,
    redirect,
    render,
)
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from data_imports.models import (
    ImportChangeLog,
    ImportRowResult,
)
from sales.models import Order

from .customer_profile360_forms import CustomerAddressForm
from .models import (
    Activity,
    Customer,
    CustomerAddress,
    Deal,
)
from .views import (
    _build_customer_smart_context,
    duplicate_last_customer_order,
)


VALID_TABS = {
    "summary",
    "addresses",
    "contact",
    "orders",
}


def _request_company(request):
    profile = getattr(
        request.user,
        "profile",
        None,
    )

    company = getattr(
        profile,
        "company",
        None,
    )

    if company:
        return company

    return getattr(
        request.user,
        "company",
        None,
    )


def _profile_url(
    customer_id,
    tab="summary",
):
    base_url = reverse(
        "customer_smart_profile",
        kwargs={
            "customer_id": customer_id,
        },
    )

    return f"{base_url}?tab={tab}#{tab}"


def _initials(customer):
    source = (
        customer.business_name
        or customer.full_name
        or "Cliente"
    )

    words = [
        word
        for word in source.replace("-", " ").split()
        if word
    ]

    if not words:
        return "CL"

    if len(words) == 1:
        return words[0][:2].upper()

    return (
        words[0][0]
        + words[1][0]
    ).upper()


def _days_since(value):
    if not value:
        return None

    if timezone.is_aware(value):
        value_date = timezone.localtime(
            value
        ).date()
    else:
        value_date = value.date()

    return max(
        0,
        (
            timezone.localdate()
            - value_date
        ).days,
    )


def _segment_customer(
    customer,
    context,
    days_since_last_order,
):
    total_purchased = Decimal(
        str(
            context.get("total_purchased")
            or "0"
        )
    )

    pending_balance = Decimal(
        str(
            context.get("pending_balance")
            or "0"
        )
    )

    days_pending = int(
        context.get("days_pending")
        or 0
    )

    orders_count = int(
        context.get("orders_count")
        or 0
    )

    if pending_balance > 0 and days_pending >= 7:
        return {
            "label": "Moroso",
            "class": "danger",
            "description": (
                "Tiene saldo vencido y requiere seguimiento de cobranza."
            ),
        }

    if (
        days_since_last_order is not None
        and days_since_last_order >= 90
    ):
        return {
            "label": "En riesgo",
            "class": "warning",
            "description": (
                "Superó 90 días sin comprar. Conviene iniciar reactivación."
            ),
        }

    if (
        customer.customer_type == "vip"
        or total_purchased >= Decimal("100000")
    ):
        return {
            "label": "VIP",
            "class": "success",
            "description": (
                "Cliente de alto valor por volumen o clasificación comercial."
            ),
        }

    if orders_count == 0:
        return {
            "label": "Nuevo",
            "class": "info",
            "description": (
                "Aún no tiene pedidos registrados."
            ),
        }

    return {
        "label": "Regular",
        "class": "primary",
        "description": (
            "Cliente activo con comportamiento comercial estable."
        ),
    }


def _order_progress(status):
    normalized = str(
        status or ""
    ).strip().lower()

    progress_map = {
        "draft": 8,
        "pending": 15,
        "quotation": 15,
        "confirmed": 25,
        "approved": 30,
        "paid": 38,
        "picking": 52,
        "preparing": 52,
        "packed": 68,
        "ready": 74,
        "shipped": 86,
        "in_transit": 88,
        "delivered": 100,
        "completed": 100,
        "cancelled": 0,
        "canceled": 0,
    }

    return progress_map.get(
        normalized,
        20,
    )


def _order_status_class(status):
    normalized = str(
        status or ""
    ).strip().lower()

    if normalized in {
        "delivered",
        "completed",
    }:
        return "success"

    if normalized in {
        "cancelled",
        "canceled",
    }:
        return "danger"

    if normalized in {
        "shipped",
        "in_transit",
        "packed",
        "ready",
    }:
        return "info"

    if normalized in {
        "paid",
        "confirmed",
        "approved",
    }:
        return "primary"

    return "warning"


def _import_context(
    company,
    customer,
    has_addresses,
):
    object_pk = str(
        customer.pk
    )

    imported_row = (
        ImportRowResult.objects.for_company(
            company
        )
        .select_related(
            "job"
        )
        .filter(
            matched_model="crm.Customer",
            matched_object_id=object_pk,
        )
        .order_by(
            "-updated_at"
        )
        .first()
    )

    change_log = (
        ImportChangeLog.objects.for_company(
            company
        )
        .select_related(
            "job",
            "row_result",
        )
        .filter(
            model_label__iexact="crm.Customer",
            object_pk=object_pk,
        )
        .order_by(
            "-created_at"
        )
        .first()
    )

    omission_row = (
        ImportRowResult.objects.for_company(
            company
        )
        .select_related(
            "job"
        )
        .filter(
            matched_model="crm.Customer",
            matched_object_id=object_pk,
            action_taken__icontains="address_omitted",
        )
        .order_by(
            "-updated_at"
        )
        .first()
    )

    source_row = (
        omission_row
        or imported_row
        or getattr(
            change_log,
            "row_result",
            None,
        )
    )

    import_job = (
        getattr(
            source_row,
            "job",
            None,
        )
        or getattr(
            change_log,
            "job",
            None,
        )
    )

    return {
        "was_imported": bool(
            imported_row
            or change_log
        ),
        "import_source_label": (
            "Importación masiva"
            if imported_row or change_log
            else customer.get_source_display()
        ),
        "has_address_omission": bool(
            omission_row
            and not has_addresses
        ),
        "import_omission_row": omission_row,
        "import_job": import_job,
    }


@login_required(login_url="/accounts/login/")
def crm_home_secure(request):
    company = _request_company(
        request
    )

    if not company:
        return HttpResponseForbidden(
            "Tu usuario no tiene una empresa asignada."
        )

    customers_qs = Customer.objects.filter(
        company=company
    )

    deals_qs = Deal.objects.filter(
        company=company
    )

    activities_qs = Activity.objects.filter(
        company=company
    )

    context = {
        "customers": customers_qs.order_by(
            "-created_at"
        )[:20],
        "total_customers": customers_qs.count(),
        "total_leads": customers_qs.filter(
            customer_type="lead"
        ).count(),
        "total_vip": customers_qs.filter(
            customer_type="vip"
        ).count(),
        "open_deals": deals_qs.filter(
            status="open"
        ).count(),
        "recent_activities": activities_qs.order_by(
            "-created_at"
        )[:8],
    }

    return render(
        request,
        "crm/home.html",
        context,
    )


@login_required(login_url="/accounts/login/")
def customer_profile_360_by_phone(
    request,
    phone=None,
):
    company = _request_company(
        request
    )

    if not company:
        return HttpResponseForbidden(
            "Tu usuario no tiene una empresa asignada."
        )

    raw_phone = str(
        phone
        or request.GET.get(
            "phone",
            "",
        )
    ).strip()

    normalized = "".join(
        character
        for character in raw_phone
        if character.isdigit()
    )

    if not normalized:
        messages.error(
            request,
            "Captura un teléfono o WhatsApp para buscar al cliente.",
        )

        return redirect(
            "crm_home"
        )

    last_digits = normalized[
        -10:
    ]

    customer = (
        Customer.objects.filter(
            company=company
        )
        .filter(
            Q(
                phone__icontains=raw_phone
            )
            | Q(
                whatsapp__icontains=raw_phone
            )
            | Q(
                phone__icontains=normalized
            )
            | Q(
                whatsapp__icontains=normalized
            )
            | Q(
                phone__icontains=last_digits
            )
            | Q(
                whatsapp__icontains=last_digits
            )
        )
        .order_by(
            "-updated_at"
        )
        .first()
    )

    if not customer:
        messages.error(
            request,
            (
                "No se encontró un cliente de esta empresa "
                f"con el teléfono {raw_phone}."
            ),
        )

        return redirect(
            "crm_home"
        )

    return redirect(
        "customer_smart_profile",
        customer_id=customer.pk,
    )


@login_required(login_url="/accounts/login/")
@require_POST
def duplicate_last_customer_order_secure(
    request,
    customer_id,
):
    company = _request_company(
        request
    )

    if not company:
        return HttpResponseForbidden(
            "Tu usuario no tiene una empresa asignada."
        )

    get_object_or_404(
        Customer,
        pk=customer_id,
        company=company,
    )

    return duplicate_last_customer_order(
        request,
        customer_id,
    )


@login_required(login_url="/accounts/login/")
def customer_profile_360(
    request,
    customer_id,
):
    company = _request_company(
        request
    )

    if not company:
        return HttpResponseForbidden(
            "Tu usuario no tiene una empresa asignada."
        )

    customer = get_object_or_404(
        Customer.objects.select_related(
            "company",
            "assigned_to",
        ).prefetch_related(
            "customer_tags",
            "customer_tags__tag",
        ),
        pk=customer_id,
        company=company,
    )

    context = _build_customer_smart_context(
        request,
        customer,
    )

    addresses = list(
        CustomerAddress.objects.filter(
            company=company,
            customer=customer,
        ).order_by(
            "-is_default",
            "label",
            "id",
        )
    )

    address_rows = []

    for address in addresses:
        address_rows.append(
            {
                "address": address,
                "maps_url": (
                    "https://www.google.com/maps/search/?api=1&query="
                    + quote_plus(
                        address.complete_address
                    )
                ),
            }
        )

    activities = (
        Activity.objects.select_related(
            "assigned_to",
            "deal",
        )
        .filter(
            company=company,
            customer=customer,
        )
        .order_by(
            "-created_at"
        )[:15]
    )

    orders_qs = (
        Order.objects.filter(
            company=company,
            customer=customer,
        )
        .exclude(
            status="cancelled"
        )
        .order_by(
            "-created_at"
        )
    )

    totals = orders_qs.aggregate(
        first_order_at=Min(
            "created_at"
        ),
        total=Sum(
            "total"
        ),
    )

    first_order_at = totals[
        "first_order_at"
    ]

    if first_order_at:
        today = timezone.localdate()
        if timezone.is_aware(
            first_order_at
        ):
            first_date = timezone.localtime(
                first_order_at
            ).date()
        else:
            first_date = first_order_at.date()

        active_months = max(
            1,
            (
                today.year
                - first_date.year
            )
            * 12
            + today.month
            - first_date.month
            + 1,
        )

        monthly_purchase = (
            Decimal(
                str(
                    totals["total"]
                    or "0"
                )
            )
            / Decimal(
                active_months
            )
        )

        monthly_purchase_source = (
            "Promedio real de pedidos"
        )

    else:
        monthly_purchase = Decimal(
            str(
                customer.estimated_monthly_purchase
                or "0"
            )
        )

        monthly_purchase_source = (
            "Estimación comercial"
        )

    days_since_last_order = _days_since(
        context.get(
            "last_order_date"
        )
    )

    segment = _segment_customer(
        customer,
        context,
        days_since_last_order,
    )

    credit_limit = getattr(
        customer,
        "credit_limit",
        None,
    )

    credit_days = getattr(
        customer,
        "credit_days",
        None,
    )

    credit_available = None

    if credit_limit is not None:
        credit_available = max(
            Decimal("0"),
            Decimal(
                str(
                    credit_limit
                    or "0"
                )
            )
            - Decimal(
                str(
                    context.get(
                        "pending_balance"
                    )
                    or "0"
                )
            ),
        )

    whatsapp_number = "".join(
        character
        for character in str(
            customer.whatsapp
            or customer.phone
            or ""
        )
        if character.isdigit()
    )

    order_history = context.get(
        "order_history",
        [],
    )

    for row in order_history:
        order = row["order"]
        row["progress"] = _order_progress(
            getattr(
                order,
                "status",
                "",
            )
        )
        row["status_class"] = _order_status_class(
            getattr(
                order,
                "status",
                "",
            )
        )

    active_tab = request.GET.get(
        "tab",
        "summary",
    )

    if active_tab not in VALID_TABS:
        active_tab = "summary"

    import_context = _import_context(
        company=company,
        customer=customer,
        has_addresses=bool(
            addresses
        ),
    )

    custom_field_rows = list(
        customer.custom_field_values
        .select_related("definition")
        .filter(
            company=company,
            definition__company=company,
            definition__is_active=True,
        )
        .order_by(
            "definition__order",
            "definition__name",
        )
    )

    context.update(
        {
            "company": company,
            "active_tab": active_tab,
            "initials": _initials(
                customer
            ),
            "segment": segment,
            "monthly_purchase": monthly_purchase,
            "monthly_purchase_source": monthly_purchase_source,
            "days_since_last_order": days_since_last_order,
            "addresses": addresses,
            "address_rows": address_rows,
            "address_count": len(
                addresses
            ),
            "activities": activities,
            "whatsapp_number": whatsapp_number,
            "business_sector": (
                getattr(
                    customer,
                    "business_sector",
                    "",
                )
                or "Sin giro registrado"
            ),
            "assigned_salesperson": (
                customer.assigned_to.get_full_name()
                or customer.assigned_to.get_username()
                if customer.assigned_to
                else "Sin vendedor asignado"
            ),
            "credit_limit": credit_limit,
            "has_credit_limit": (
                credit_limit is not None
            ),
            "credit_days": credit_days,
            "credit_available": credit_available,
            "order_history": order_history,
            "custom_field_rows": custom_field_rows,
            **import_context,
        }
    )

    return render(
        request,
        "crm/customer_smart_profile.html",
        context,
    )


@login_required(login_url="/accounts/login/")
@transaction.atomic
def customer_address_create(
    request,
    customer_id,
):
    company = _request_company(
        request
    )

    if not company:
        return HttpResponseForbidden(
            "Tu usuario no tiene una empresa asignada."
        )

    customer = get_object_or_404(
        Customer.objects.select_for_update(),
        pk=customer_id,
        company=company,
    )

    if request.method == "POST":
        form = CustomerAddressForm(
            request.POST
        )

        if form.is_valid():
            address = form.save(
                commit=False
            )

            address.company = company
            address.customer = customer
            address.save()

            messages.success(
                request,
                (
                    f'Dirección "{address.label or address.get_address_type_display()}" '
                    "guardada correctamente."
                ),
            )

            return redirect(
                _profile_url(
                    customer.pk,
                    "addresses",
                )
            )

    else:
        form = CustomerAddressForm(
            initial={
                "contact_name": (
                    customer.contact_name
                    or customer.full_name
                ),
                "phone": (
                    customer.whatsapp
                    or customer.phone
                    or ""
                ),
                "email": (
                    customer.email
                    or ""
                ),
                "country": "México",
                "country_code": "MX",
                "is_default": (
                    not CustomerAddress.objects.filter(
                        company=company,
                        customer=customer,
                    ).exists()
                ),
            }
        )

    return render(
        request,
        "crm/customer_address_form.html",
        {
            "company": company,
            "customer": customer,
            "form": form,
            "mode": "create",
        },
    )


@login_required(login_url="/accounts/login/")
@transaction.atomic
def customer_address_edit(
    request,
    customer_id,
    address_id,
):
    company = _request_company(
        request
    )

    if not company:
        return HttpResponseForbidden(
            "Tu usuario no tiene una empresa asignada."
        )

    customer = get_object_or_404(
        Customer.objects.select_for_update(),
        pk=customer_id,
        company=company,
    )

    address = get_object_or_404(
        CustomerAddress.objects.select_for_update(),
        pk=address_id,
        customer=customer,
        company=company,
    )

    if request.method == "POST":
        form = CustomerAddressForm(
            request.POST,
            instance=address,
        )

        if form.is_valid():
            updated_address = form.save(
                commit=False
            )

            updated_address.company = company
            updated_address.customer = customer
            updated_address.save()

            messages.success(
                request,
                (
                    f'Dirección "{updated_address.label or updated_address.get_address_type_display()}" '
                    "actualizada correctamente."
                ),
            )

            return redirect(
                _profile_url(
                    customer.pk,
                    "addresses",
                )
            )

    else:
        form = CustomerAddressForm(
            instance=address
        )

    return render(
        request,
        "crm/customer_address_form.html",
        {
            "company": company,
            "customer": customer,
            "address": address,
            "form": form,
            "mode": "edit",
        },
    )


@login_required(login_url="/accounts/login/")
@require_POST
@transaction.atomic
def customer_address_set_default(
    request,
    customer_id,
    address_id,
):
    company = _request_company(
        request
    )

    if not company:
        return HttpResponseForbidden(
            "Tu usuario no tiene una empresa asignada."
        )

    customer = get_object_or_404(
        Customer.objects.select_for_update(),
        pk=customer_id,
        company=company,
    )

    address = get_object_or_404(
        CustomerAddress.objects.select_for_update(),
        pk=address_id,
        customer=customer,
        company=company,
    )

    address.is_default = True
    address.save()

    messages.success(
        request,
        (
            f'"{address.label or address.get_address_type_display()}" '
            "ahora es la dirección principal para este tipo."
        ),
    )

    return redirect(
        _profile_url(
            customer.pk,
            "addresses",
        )
    )
