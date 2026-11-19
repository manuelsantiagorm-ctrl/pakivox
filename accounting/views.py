import sentry_sdk
import calendar
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.http import HttpResponse
from django.utils.text import slugify
from accounts.capability_access import user_has_capability
from .services.excel_exports import build_financial_workbook
from .forms import BalanceReconciliationForm, ExpenseForm
from .models import (
    AccountingAccount,
    Expense,
    ExpenseCategory,
    JournalEntry,
    JournalLine,
)
from django.conf import settings
from django.views.decorators.http import require_http_methods
from subscriptions.ai_quota import (
    AIAccessDenied,
    AIQuotaExhausted,
    complete_ai_query,
    get_ai_quota_summary,
    refund_ai_query,
    reserve_ai_query,
)
from .services.gemini_financial_advisor import (
    FinancialAIError,
    analyze_finances_with_gemini,
)
from .services.financial_statements import (
    build_balance_sheet,
    build_income_statement,
)


def get_user_company(user):
    profile = getattr(user, "profile", None)

    if profile:
        company = getattr(profile, "company", None)

        if company:
            return company

    return getattr(user, "company", None)


def require_company(user):
    company = get_user_company(user)

    if not company:
        raise PermissionDenied("Tu usuario no tiene una empresa asignada.")

    return company


def can_manage_accounting(user):
    return user_has_capability(user, "accounting.manage")


def resolve_period(request):
    today = timezone.localdate()
    default_start = today.replace(day=1)

    start_date = parse_date(request.GET.get("start_date", ""))

    end_date = parse_date(request.GET.get("end_date", ""))

    if not start_date:
        start_date = default_start

    if not end_date:
        end_date = today

    if start_date > end_date:
        messages.warning(
            request,
            (
                "La fecha inicial no puede ser mayor "
                "que la fecha final. Se usó el mes actual."
            ),
        )

        start_date = default_start
        end_date = today

    return start_date, end_date


def resolve_as_of_date(request):
    today = timezone.localdate()

    as_of_date = parse_date(request.GET.get("as_of_date", ""))

    return as_of_date or today


@login_required(login_url="/admin/login/")
def financial_dashboard(request):
    company = require_company(request.user)

    start_date, end_date = resolve_period(request)

    income_statement = build_income_statement(
        company=company,
        start_date=start_date,
        end_date=end_date,
    )

    balance_sheet = build_balance_sheet(
        company=company,
        as_of_date=end_date,
    )

    recent_expenses = (
        Expense.objects.filter(
            company=company,
        )
        .select_related(
            "category",
            "payment_account",
        )
        .order_by(
            "-expense_date",
            "-id",
        )[:8]
    )

    return render(
        request,
        "accounting/financial_dashboard.html",
        {
            "company": company,
            "start_date": start_date,
            "end_date": end_date,
            "income_statement": income_statement,
            "balance_sheet": balance_sheet,
            "recent_expenses": recent_expenses,
            "can_manage_accounting": can_manage_accounting(request.user),
        },
    )


@login_required(login_url="/admin/login/")
def income_statement(request):
    company = require_company(request.user)

    start_date, end_date = resolve_period(request)

    statement = build_income_statement(
        company=company,
        start_date=start_date,
        end_date=end_date,
    )

    return render(
        request,
        "accounting/income_statement.html",
        {
            "company": company,
            "start_date": start_date,
            "end_date": end_date,
            "statement": statement,
        },
    )


@login_required(login_url="/admin/login/")
def balance_sheet(request):
    company = require_company(request.user)

    as_of_date = resolve_as_of_date(request)

    statement = build_balance_sheet(
        company=company,
        as_of_date=as_of_date,
    )

    return render(
        request,
        "accounting/balance_sheet.html",
        {
            "company": company,
            "as_of_date": as_of_date,
            "statement": statement,
            "can_manage_accounting": can_manage_accounting(request.user),
            "reconciliation_form": BalanceReconciliationForm(
                company=company,
                initial={"as_of_date": as_of_date},
            ),
        },
    )


@login_required(login_url="/admin/login/")
@require_POST
def reconcile_balance(request):
    company = require_company(request.user)
    as_of_date = (
        parse_date(request.POST.get("as_of_date", ""))
        or timezone.localdate()
    )

    # Bloqueo temporal intencional: la diferencia actual combina módulos
    # operativos que todavía no generan todas sus pólizas automáticas.
    messages.error(
        request,
        (
            "Conciliación bloqueada por seguridad. La diferencia contiene "
            "saldos operativos pendientes de integración contable. Primero "
            "deben generarse las pólizas automáticas de la fase 4."
        ),
    )

    return redirect(
        f"{reverse('accounting:balance_sheet')}"
        f"?as_of_date={as_of_date.isoformat()}"
    )


@login_required(login_url="/admin/login/")
def expense_list(request):
    company = require_company(request.user)

    start_date, end_date = resolve_period(request)

    period_days = (end_date - start_date).days + 1
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)

    period_expenses = Expense.objects.filter(
        company=company,
        expense_date__range=(start_date, end_date),
    )

    posted_expenses = period_expenses.filter(status=Expense.STATUS_POSTED)
    posted_total = posted_expenses.aggregate(total=Sum("amount"))["total"] or 0
    posted_count = posted_expenses.count()
    draft_count = period_expenses.filter(status=Expense.STATUS_DRAFT).count()
    cancelled_count = period_expenses.filter(
        status=Expense.STATUS_CANCELLED
    ).count()
    average_expense = posted_total / posted_count if posted_count else 0

    previous_total = (
        Expense.objects.filter(
            company=company,
            expense_date__range=(previous_start, previous_end),
            status=Expense.STATUS_POSTED,
        ).aggregate(total=Sum("amount"))["total"]
        or 0
    )
    if previous_total:
        period_change_pct = ((posted_total - previous_total) / previous_total) * 100
        period_change_is_new = False
    elif posted_total:
        period_change_pct = 0
        period_change_is_new = True
    else:
        period_change_pct = 0
        period_change_is_new = False

    category_totals = list(
        posted_expenses.values("category_id", "category__name")
        .annotate(total=Sum("amount"), movements=Count("id"))
        .order_by("-total", "category__name")
    )
    for category in category_totals:
        category["percentage"] = (
            (category["total"] / posted_total) * 100 if posted_total else 0
        )
    top_category = category_totals[0] if category_totals else None

    search_query = (request.GET.get("q", "") or "").strip()
    status_filter = (request.GET.get("status", "") or "").strip()
    category_filter = (request.GET.get("category", "") or "").strip()

    expenses = period_expenses
    if search_query:
        expenses = expenses.filter(
            Q(description__icontains=search_query)
            | Q(supplier_name__icontains=search_query)
            | Q(reference__icontains=search_query)
        )
    if status_filter in dict(Expense.STATUS_CHOICES):
        expenses = expenses.filter(status=status_filter)
    else:
        status_filter = ""
    if category_filter.isdigit():
        expenses = expenses.filter(category_id=int(category_filter))
    else:
        category_filter = ""

    expenses = (
        expenses
        .select_related(
            "category",
            "payment_account",
            "created_by",
            "posted_by",
        )
        .order_by(
            "-expense_date",
            "-id",
        )
    )

    paginator = Paginator(expenses, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)

    categories = (
        ExpenseCategory.objects.filter(company=company, is_active=True)
        .order_by("name")
    )
    today = timezone.localdate()
    current_month_start = today.replace(day=1)
    previous_month_end = current_month_start - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)
    quarter_start_month = ((today.month - 1) // 3) * 3 + 1
    quarter_start = today.replace(month=quarter_start_month, day=1)

    return render(
        request,
        "accounting/expense_list.html",
        {
            "company": company,
            "start_date": start_date,
            "end_date": end_date,
            "expenses": page_obj.object_list,
            "page_obj": page_obj,
            "query_string": query_params.urlencode(),
            "posted_total": posted_total,
            "posted_count": posted_count,
            "draft_count": draft_count,
            "cancelled_count": cancelled_count,
            "average_expense": average_expense,
            "previous_total": previous_total,
            "period_change_pct": period_change_pct,
            "period_change_is_new": period_change_is_new,
            "category_totals": category_totals[:5],
            "top_category": top_category,
            "categories": categories,
            "search_query": search_query,
            "status_filter": status_filter,
            "category_filter": category_filter,
            "today": today,
            "current_month_start": current_month_start,
            "previous_month_start": previous_month_start,
            "previous_month_end": previous_month_end,
            "quarter_start": quarter_start,
            "can_manage_accounting": can_manage_accounting(request.user),
        },
    )


@login_required(login_url="/admin/login/")
def expense_create(request):
    company = require_company(request.user)

    if request.method == "POST":
        form = ExpenseForm(
            request.POST,
            request.FILES,
            company=company,
        )

        if form.is_valid():
            try:
                expense = form.save(commit=False)

                expense.company = company
                expense.created_by = request.user

                expense.full_clean()
                expense.save()

                expense.post(
                    user=request.user,
                )

                messages.success(
                    request,
                    ("El gasto fue registrado y " "contabilizado correctamente."),
                )

                return redirect("accounting:expense_list")

            except ValidationError as exc:
                form.add_error(
                    None,
                    " ".join(exc.messages),
                )

    else:
        form = ExpenseForm(
            company=company,
        )

    return render(
        request,
        "accounting/expense_form.html",
        {
            "company": company,
            "form": form,
        },
    )


@login_required(login_url="/admin/login/")
def export_financial_excel(request):
    company = require_company(request.user)

    as_of_date = parse_date(request.GET.get("as_of_date", ""))

    if as_of_date:
        end_date = as_of_date

        start_date = parse_date(request.GET.get("start_date", ""))

        if not start_date:
            start_date = end_date.replace(day=1)

    else:
        start_date, end_date = resolve_period(request)

    if start_date > end_date:
        start_date = end_date.replace(day=1)

    workbook = build_financial_workbook(
        company=company,
        start_date=start_date,
        end_date=end_date,
    )

    company_slug = slugify(company.name) or f"empresa-{company.pk}"

    filename = (
        f"wms360-finanzas-"
        f"{company_slug}-"
        f"{start_date.isoformat()}-"
        f"{end_date.isoformat()}.xlsx"
    )

    response = HttpResponse(
        workbook.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument." "spreadsheetml.sheet"
        ),
    )

    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    response["Cache-Control"] = "private, no-store, no-cache, must-revalidate"

    return response


@login_required(login_url="/admin/login/")
@require_POST
def expense_cancel(request, expense_id):
    company = require_company(request.user)

    expense = get_object_or_404(
        Expense,
        pk=expense_id,
        company=company,
    )

    try:
        expense.cancel(
            user=request.user,
        )

        messages.success(
            request,
            "El gasto fue cancelado y revertido.",
        )

    except ValidationError as exc:
        messages.error(
            request,
            " ".join(exc.messages),
        )

    return redirect("accounting:expense_list")


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def financial_ai_advisor(request):
    company = require_company(request.user)

    today = timezone.localdate()
    default_start = today.replace(day=1)

    source = request.POST if request.method == "POST" else request.GET

    period_preset = (source.get("period_preset", "") or "").strip()

    if period_preset == "current_month":
        start_date = default_start
        end_date = today
    elif period_preset == "previous_month":
        previous_month_end = default_start - timedelta(days=1)
        start_date = previous_month_end.replace(day=1)
        end_date = previous_month_end
    elif period_preset == "quarter":
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        start_date = today.replace(month=quarter_start_month, day=1)
        end_date = today
    else:
        period_preset = "custom"
        start_date = parse_date(source.get("start_date", "")) or default_start
        end_date = parse_date(source.get("end_date", "")) or today

    if start_date > end_date:
        messages.warning(
            request,
            ("La fecha inicial no puede superar " "la fecha final."),
        )

        start_date = default_start
        end_date = today
        period_preset = "current_month"

    question = (source.get("question", "") or "").strip()

    statement = build_income_statement(
        company=company,
        start_date=start_date,
        end_date=end_date,
    )

    balance = build_balance_sheet(
        company=company,
        as_of_date=end_date,
    )

    period_days = (end_date - start_date).days + 1
    period_label = (
        f"{start_date.strftime('%d/%m/%Y')} al "
        f"{end_date.strftime('%d/%m/%Y')}"
    )
    previous = statement.get("previous") or {}
    changes = statement.get("changes") or {}
    kpi_changes = {
        "net_sales": changes.get("net_sales"),
        "gross_profit": changes.get("gross_profit"),
        "operating_expenses": changes.get("operating_expenses"),
        "net_profit": changes.get("net_profit"),
    }
    month_last_day = calendar.monthrange(today.year, today.month)[1]
    suggested_cutoff = today.replace(day=month_last_day)

    ai_result = None
    ai_quota = get_ai_quota_summary(company)

    if request.method == "POST":
        ai_usage = None

        try:
            ai_usage = reserve_ai_query(
                company=company,
                user=request.user,
                question=question,
                mode="financial",
                warehouse_scope="company_wide",
            )

            result = analyze_finances_with_gemini(
                company=company,
                start_date=start_date,
                end_date=end_date,
                user_question=question,
            )

            ai_result = result["analysis"]
            complete_ai_query(ai_usage)
            ai_quota = get_ai_quota_summary(company)

            messages.success(
                request,
                "Gemini terminó el análisis financiero.",
            )

        except AIQuotaExhausted as exc:
            ai_quota = get_ai_quota_summary(company)
            messages.error(
                request,
                str(exc),
            )

        except AIAccessDenied as exc:
            ai_quota = get_ai_quota_summary(company)
            messages.error(
                request,
                str(exc),
            )

        except FinancialAIError as exc:
            if ai_usage:
                refund_ai_query(
                    ai_usage,
                    error_code="financial_ai_error",
                )

            ai_quota = get_ai_quota_summary(company)
            messages.error(
                request,
                str(exc),
            )

        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            if ai_usage:
                refund_ai_query(
                    ai_usage,
                    error_code=exc.__class__.__name__,
                )

            ai_quota = get_ai_quota_summary(company)

            messages.error(
                request,
                "No fue posible completar el análisis financiero.",
            )

    return render(
        request,
        "accounting/financial_ai.html",
        {
            "company": company,
            "start_date": start_date,
            "end_date": end_date,
            "question": question,
            "statement": statement,
            "balance": balance,
            "period_preset": period_preset,
            "period_label": period_label,
            "period_days": period_days,
            "previous": previous,
            "kpi_changes": kpi_changes,
            "suggested_cutoff": suggested_cutoff,
            "ai_result": ai_result,
            "ai_quota": ai_quota,
            "gemini_enabled": (
                settings.GEMINI_FINANCIAL_AI_ENABLED
                and bool(settings.GEMINI_API_KEY)
                and ai_quota["allowed"]
                and (
                    ai_quota["remaining"] is None
                    or ai_quota["remaining"] > 0
                )
            ),
            "gemini_model": settings.GEMINI_MODEL,
        },
    )
