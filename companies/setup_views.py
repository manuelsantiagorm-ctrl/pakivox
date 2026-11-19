from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from accounts.models import UserProfile, UserWarehouseAccess
from inventory.models import Product, Stock, Warehouse
from shipping_hub.models import CompanyShippingIntegration
from warehouse.models import WarehouseLocation


def _get_user_company(user):
    """
    Devuelve exclusivamente la empresa asociada al usuario autenticado.

    Nunca acepta company_id desde GET, POST o parámetros externos.
    """

    if not user or not getattr(user, "is_authenticated", False):
        return None

    profile = getattr(user, "profile", None) or getattr(user, "userprofile", None)

    if profile and getattr(profile, "company_id", None):
        return profile.company

    company = getattr(user, "company", None)

    if company and getattr(company, "pk", None):
        return company

    return None


def puede_configurar_empresa(user):
    """Las pantallas de puesta en marcha (estado de la configuración, plan y
    suscripción) son para quien administra la empresa, no para cada
    trabajador. Un trabajador sin ese permiso vuelve a su inicio."""
    from accounts.capability_access import (
        get_active_user_profile,
        user_has_capability,
    )

    profile = get_active_user_profile(user)
    if profile is None:
        return False
    if profile.role in {"owner", "admin"}:
        return True
    return user_has_capability(user, "company.configure")


@login_required(login_url="accounts:login")
def initial_setup(request):
    """
    Centro de configuración inicial de WMS360.

    Solo consulta datos de la empresa del usuario autenticado.
    No modifica datos.
    """

    company = _get_user_company(request.user)

    if company is None:
        return redirect("accounts:onboarding_empresa")

    if not puede_configurar_empresa(request.user):
        return redirect("accounts:employee_home")

    # ==========================================================
    # ESTADO REAL DE CONFIGURACIÓN
    # ==========================================================

    company_ready = bool(company and company.pk and company.is_active)

    warehouse_ready = Warehouse.objects.filter(
        company=company,
        is_active=True,
    ).exists()

    products_ready = Product.objects.filter(
        company=company,
        is_active=True,
    ).exists()

    locations_ready = WarehouseLocation.objects.filter(
        company=company,
        is_active=True,
    ).exists()

    stock_ready = Stock.objects.filter(
        company=company,
        physical_stock__gt=0,
    ).exists()

    # Consideramos que hay equipo adicional si existe
    # otro perfil activo distinto al usuario actual.
    team_ready = (
        UserProfile.objects.filter(
            company=company,
            is_active=True,
        )
        .exclude(
            user=request.user,
        )
        .exists()
    )

    warehouse_access_ready = UserWarehouseAccess.objects.filter(
        company=company,
        is_active=True,
    ).exists()

    shipping_ready = CompanyShippingIntegration.objects.filter(
        company=company,
        is_active=True,
    ).exists()

    # ==========================================================
    # PASOS ESENCIALES
    # ==========================================================

    essential_steps = [
        {
            "key": "company",
            "title": "Empresa creada",
            "description": (
                "Tu espacio de trabajo ya está asociado " "correctamente a WMS360."
            ),
            "complete": company_ready,
            "url_name": "companies:profile",
            "icon": "bi-building-check",
        },
        {
            "key": "warehouse",
            "title": "Registra tu primera bodega",
            "description": (
                "Define la ubicación desde donde controlarás "
                "inventario y operaciones."
            ),
            "complete": warehouse_ready,
            "url_name": "companies:warehouse_create",
            "icon": "bi-buildings",
        },
        {
            "key": "products",
            "title": "Crea o importa tus productos",
            "description": (
                "Agrega tu catálogo comercial y los SKUs " "que manejará tu empresa."
            ),
            "complete": products_ready,
            "url_name": "inventory:crear_producto",
            "icon": "bi-box-seam",
        },
        {
            "key": "locations",
            "title": "Configura ubicaciones",
            "description": (
                "Organiza pasillos, racks, niveles y posiciones "
                "físicas de tu almacén."
            ),
            "complete": locations_ready,
            "url_name": "warehouse:crear_ubicacion",
            "icon": "bi-geo-alt",
        },
        {
            "key": "stock",
            "title": "Registra inventario inicial",
            "description": (
                "Carga las existencias físicas con las que " "comenzará tu operación."
            ),
            "complete": stock_ready,
            "url_name": "inventory:lista_stock",
            "icon": "bi-boxes",
        },
    ]

    # ==========================================================
    # PASOS RECOMENDADOS
    # ==========================================================

    optional_steps = [
        {
            "key": "team",
            "title": "Invita a tu equipo",
            "description": (
                "Agrega usuarios y configura sus permisos " "de acceso a las bodegas."
            ),
            "complete": team_ready or warehouse_access_ready,
            "url_name": "accounts:employee_invitation_create",
            "icon": "bi-people",
        },
        {
            "key": "shipping",
            "title": "Configura tus envíos",
            "description": (
                "Conecta Shipping Hub cuando estés listo "
                "para cotizar y generar guías."
            ),
            "complete": shipping_ready,
            "url_name": None,
            "icon": "bi-truck",
        },
    ]

    essential_completed = sum(1 for step in essential_steps if step["complete"])

    essential_total = len(essential_steps)

    progress_percent = (
        round((essential_completed / essential_total) * 100) if essential_total else 0
    )

    ready_to_operate = essential_completed == essential_total

    context = {
        "setup_company": company,
        "essential_steps": essential_steps,
        "optional_steps": optional_steps,
        "essential_completed": essential_completed,
        "essential_total": essential_total,
        "progress_percent": progress_percent,
        "ready_to_operate": ready_to_operate,
        "team_ready": team_ready,
        "shipping_ready": shipping_ready,
    }

    return render(
        request,
        "companies/initial_setup.html",
        context,
    )
