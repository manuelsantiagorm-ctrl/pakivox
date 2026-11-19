from django.urls import path

from accounts import views
from accounts.warehouse_views import (
    seleccionar_bodega_activa,
)
from .views import onboarding_empresa_view
from accounts.employee_home_views import employee_home

app_name = "accounts"


urlpatterns = [
    # Registro público de empresas
    path(
        "registro/",
        views.company_register,
        name="company_register",
    ),
    path(
        "registro/correo-enviado/",
        views.registration_email_sent,
        name="registration_email_sent",
    ),
    path(
        "registro/verificar/",
        views.verify_company_registration,
        name="verify_company_registration",
    ),
    # Autenticación
    path(
        "login/",
        views.client_login,
        name="login",
    ),
    path(
        "logout/",
        views.client_logout,
        name="logout",
    ),
    # Contexto de bodega activa
    path(
        "seleccionar-bodega/",
        seleccionar_bodega_activa,
        name="seleccionar_bodega_activa",
    ),
    # Invitaciones de empleados
    path(
        "equipo/invitar/",
        views.employee_invitation_create,
        name="employee_invitation_create",
    ),
    path(
        "equipo/invitacion/<int:invitation_id>/revocar/",
        views.employee_invitation_revoke,
        name="employee_invitation_revoke",
    ),
    path(
        "equipo/empleado/<int:employee_id>/actividades/",
        views.employee_activities_update,
        name="employee_activities_update",
    ),
    path(
        "equipo/empleado/<int:employee_id>/puestos/",
        views.employee_jobs_update,
        name="employee_jobs_update",
    ),
    path(
        "invitacion/<str:token>/aceptar/",
        views.accept_employee_invitation_view,
        name="accept_employee_invitation",
    ),
    path(
        "cliente-saas/<str:token>/aceptar/",
        views.accept_saas_client_invitation_view,
        name="accept_saas_client_invitation",
    ),
    path("onboarding-empresa/", onboarding_empresa_view, name="onboarding_empresa"),
    path(
        "inicio/",
        employee_home,
        name="employee_home",
    ),
]
