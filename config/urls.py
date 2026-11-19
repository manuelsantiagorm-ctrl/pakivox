from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView
from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import include, path

from accounts.capability_access import capability_required

from analytics.views import landing_page
from customer_updates.views import seguimiento_pedido
from crm.customer_edit_views import customer_create, customer_edit
from crm.customer_profile360_views import (
    crm_home_secure,
    customer_address_create,
    customer_address_edit,
    customer_address_set_default,
    customer_profile_360,
    customer_profile_360_by_phone,
    duplicate_last_customer_order_secure,
)
from crm.views import (
    add_deal_comment,
    add_deal_followup,
    complete_activity,
    create_deal_modal,
    crm_agenda,
    crm_kanban,
    crm_search_deals,
    deal_detail_modal,
    mark_deal_lost,
    mark_deal_won,
    reopen_activity,
    update_activity_due_date,
    update_deal_quick,
    update_deal_stage,
)


def protect(capability_code, view_function):
    return login_required(
        capability_required(capability_code)(view_function),
        login_url="/accounts/login/",
    )


def permission_denied_view(request, exception=None):
    # Los PermissionDenied del sistema llevan un motivo escrito para el usuario
    # ("Selecciona una bodega específica..."). Se muestra para que sepa qué
    # hacer; se limita el largo y nunca se muestran trazas ni datos internos.
    motivo = str(exception or "").strip()
    if len(motivo) > 220 or "Traceback" in motivo:
        motivo = ""
    return render(
        request,
        "403.html",
        {
            "motivo": motivo,
            "pide_bodega": "bodega" in motivo.lower(),
        },
        status=403,
    )


handler403 = permission_denied_view


urlpatterns = [
    path("shipping-hub/despacho/", include("shipping_hub.dispatch_urls")),
    # Landing pública de WMS360
    path("", landing_page, name="landing"),
    # Seguimiento público del pedido para el comprador (token secreto, sin sesión).
    path("seguimiento/<str:token>/", seguimiento_pedido, name="seguimiento_pedido"),
    # 1. Tu Login y sesión de clientes WMS360 (TU DISEÑO BONITO)
    path("accounts/", include("accounts.urls")),
    # 2. Rutas secundarias de Allauth (Google OAuth)
    # El alta local de allauth esta cerrada: quien llegue a /accounts/signup/
    # va al registro oficial de empresas (con verificacion de correo).
    path(
        "accounts/signup/",
        RedirectView.as_view(pattern_name="accounts:company_register", permanent=False),
        name="registro_allauth_redirigido",
    ),
    path("accounts/", include("allauth.urls")),
    # Perfil corporativo de la empresa cliente
    path("empresa/", include("companies.urls")),
    # CRM
    path("crm/", protect("crm.manage", crm_home_secure), name="crm_home"),
    path("crm/kanban/", protect("crm.manage", crm_kanban), name="crm_kanban"),
    path(
        "crm/kanban/update-stage/",
        protect("crm.manage", update_deal_stage),
        name="update_deal_stage",
    ),
    path(
        "crm/search-deals/",
        protect("crm.manage", crm_search_deals),
        name="crm_search_deals",
    ),
    path(
        "crm/deal/<int:deal_id>/modal/",
        protect("crm.manage", deal_detail_modal),
        name="deal_detail_modal",
    ),
    path(
        "crm/deal/<int:deal_id>/won/",
        protect("crm.manage", mark_deal_won),
        name="mark_deal_won",
    ),
    path(
        "crm/deal/<int:deal_id>/lost/",
        protect("crm.manage", mark_deal_lost),
        name="mark_deal_lost",
    ),
    path(
        "crm/deal/<int:deal_id>/comment/",
        protect("crm.manage", add_deal_comment),
        name="add_deal_comment",
    ),
    path(
        "crm/deal/create/",
        protect("crm.manage", create_deal_modal),
        name="create_deal_modal",
    ),
    path(
        "crm/deal/<int:deal_id>/quick-update/",
        protect("crm.manage", update_deal_quick),
        name="update_deal_quick",
    ),
    path(
        "crm/deal/<int:deal_id>/followup/",
        protect("crm.manage", add_deal_followup),
        name="add_deal_followup",
    ),
    path(
        "crm/agenda/",
        protect("crm.manage", crm_agenda),
        name="crm_agenda",
    ),
    path(
        "crm/activity/<int:activity_id>/complete/",
        protect("crm.manage", complete_activity),
        name="complete_activity",
    ),
    path(
        "crm/activity/<int:activity_id>/reopen/",
        protect("crm.manage", reopen_activity),
        name="reopen_activity",
    ),
    path(
        "crm/activity/<int:activity_id>/due-date/",
        protect("crm.manage", update_activity_due_date),
        name="update_activity_due_date",
    ),
    # Perfil Cliente 360
    path(
        "crm/customer/nuevo/",
        protect("crm.manage", customer_create),
        name="customer_create",
    ),
    path(
        "crm/customer/<int:customer_id>/smart-profile/",
        protect("crm.manage", customer_profile_360),
        name="customer_smart_profile",
    ),
    path(
        "crm/customer/search-phone/",
        protect("crm.manage", customer_profile_360_by_phone),
        name="customer_smart_profile_by_phone",
    ),
    path(
        "crm/customer/phone/<str:phone>/",
        protect("crm.manage", customer_profile_360_by_phone),
        name="customer_smart_profile_by_phone_direct",
    ),
    path(
        "crm/customer/<int:customer_id>/duplicate-last-order/",
        protect("crm.manage", duplicate_last_customer_order_secure),
        name="duplicate_last_customer_order",
    ),
    path(
        "crm/customer/<int:customer_id>/edit/",
        protect("crm.manage", customer_edit),
        name="customer_edit",
    ),
    # Direcciones del Perfil Cliente 360
    path(
        "crm/customer/<int:customer_id>/addresses/new/",
        protect("crm.manage", customer_address_create),
        name="customer_address_create",
    ),
    path(
        ("crm/customer/<int:customer_id>/" "addresses/<int:address_id>/edit/"),
        protect("crm.manage", customer_address_edit),
        name="customer_address_edit",
    ),
    path(
        ("crm/customer/<int:customer_id>/" "addresses/<int:address_id>/default/"),
        protect("crm.manage", customer_address_set_default),
        name="customer_address_set_default",
    ),
    # Dashboard CEO
    path("ceo/", include("dashboard.urls")),
    # Módulos operativos WMS360
    path("warehouse/", include("warehouse.urls")),
    path("sales/", include("sales.urls")),
    path("inventory/", include("inventory.urls")),
    path("notificaciones/", include("notifications.urls")),
    path("pagos/", include("payments.urls")),
    path("compras/", include("purchases.urls")),
    path("messaging/", include("messaging.urls")),
    path("subscriptions/", include("subscriptions.urls")),
    path("legal/", include("legal.urls")),
    path("shipping-hub/", include("shipping_hub.urls")),
    path("finanzas/", include("accounting.urls")),
    path("importaciones/", include("data_imports.urls")),
    path(
        "configuracion/campos-personalizados/",
        include("custom_fields.urls"),
    ),
    # Ruta de compatibilidad del dashboard
    path(
        "dashboard/",
        include(
            "dashboard.urls",
            namespace="dashboard_legacy",
        ),
    ),
    # Panel Owner SaaS
    path("owner/", include("owner.urls")),
    # Administrador Django
    path("admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )

