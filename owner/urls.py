from django.urls import path

from . import company_profile_views
from . import views

app_name = "owner"

urlpatterns = [
    path(
        "",
        views.owner_saas_console,
        name="console",
    ),
    # SOLICITUDES Y CANALES DE WHATSAPP
    path(
        "whatsapp/",
        views.whatsapp_requests_list,
        name="whatsapp_requests_list",
    ),
    path(
        "whatsapp/<int:pk>/",
        views.whatsapp_request_detail,
        name="whatsapp_request_detail",
    ),
    # PERFIL DE EMPRESA SAAS - PASO 8
    path(
        "companies/<int:company_id>/profile/",
        company_profile_views.owner_company_profile,
        name="company_profile",
    ),
    path(
        "companies/<int:company_id>/edit/",
        company_profile_views.owner_company_edit,
        name="company_edit",
    ),
]
