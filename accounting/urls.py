from django.contrib.auth.decorators import login_required
from django.urls import path

from accounts.capability_access import capability_required

from . import views



def protect(capability_code, view_function):
    return login_required(
        capability_required(capability_code)(view_function),
        login_url="/accounts/login/",
    )

app_name = "accounting"


urlpatterns = [
    path(
        "",
        protect("accounting.view", views.financial_dashboard),
        name="financial_dashboard",
    ),
    path(
        "estado-resultados/",
        protect("accounting.view", views.income_statement),
        name="income_statement",
    ),
    path(
        "balance-general/",
        protect("accounting.view", views.balance_sheet),
        name="balance_sheet",
    ),
    path(
        "balance-general/conciliar/",
        protect("accounting.manage", views.reconcile_balance),
        name="reconcile_balance",
    ),
    path(
        "gastos/",
        protect("accounting.view", views.expense_list),
        name="expense_list",
    ),
    path(
        "gastos/nuevo/",
        protect("accounting.manage", views.expense_create),
        name="expense_create",
    ),
    path(
        "gastos/<int:expense_id>/cancelar/",
        protect("accounting.manage", views.expense_cancel),
        name="expense_cancel",
    ),
    path(
        "exportar-excel/",
        protect("accounting.view", views.export_financial_excel),
        name="export_financial_excel",
    ),
    path(
        "ia/",
        protect("accounting.view", views.financial_ai_advisor),
        name="financial_ai_advisor",
    ),
]
