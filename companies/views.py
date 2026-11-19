from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied

from .models import Company
from .forms import (
    CompanyForm,
)  # Asegúrate de ajustar según el nombre de tu formulario de empresa
from warehouse.models import Warehouse
from warehouse.forms import (
    WarehouseForm,
)  # O el formulario con el que creas las bodegas


@login_required
def crear_empresa(request):
    """
    Paso 1 del Onboarding: Registra el nombre y datos iniciales de la empresa.
    """
    profile = getattr(request.user, "userprofile", None) or getattr(
        request.user, "profile", None
    )

    # Si el usuario ya tiene empresa y ya tiene bodegas, enviarlo directo al Dashboard
    if profile and profile.company:
        if Warehouse.objects.filter(company=profile.company).exists():
            return redirect("/ceo/")
        else:
            # Si ya tiene empresa pero le falta la bodega, mandarlo al Paso 2
            messages.info(
                request,
                "Tu empresa está creada. Registra tu primera bodega para completar la configuración inicial.",
            )
            return redirect("companies:warehouse_create")

    if request.method == "POST":
        form = CompanyForm(request.POST, request.FILES)
        if form.is_valid():
            empresa = form.save()

            # Enlazar la empresa al perfil del usuario
            if profile:
                profile.company = empresa
                profile.role = "owner"
                profile.save()

            messages.info(
                request,
                "¡Empresa creada con éxito! Ahora registra tu primera bodega para comenzar a operar en WMS360.",
            )
            # 🚀 Redirigir al Paso 2: Crear la primera bodega
            return redirect("companies:warehouse_create")
    else:
        form = CompanyForm()

    return render(request, "companies/company_form.html", {"form": form})


@login_required
def warehouse_create(request):
    """
    Paso 2 del Onboarding (Pantalla: /empresa/bodegas/nueva/):
    Crea la bodega y la activa en la sesión.
    """
    profile = getattr(request.user, "userprofile", None) or getattr(
        request.user, "profile", None
    )
    company = profile.company if profile else None

    # Si no tiene empresa registrada aún, regresarlo al Paso 1
    if not company:
        messages.warning(request, "Primero debes registrar el nombre de tu empresa.")
        return redirect("companies:crear_empresa")

    if request.method == "POST":
        form = WarehouseForm(request.POST)
        if form.is_valid():
            bodega = form.save(commit=False)
            bodega.company = company
            bodega.save()

            # ⚡ Marcar esta bodega como la ACTIVA en la sesión del usuario
            request.session["active_warehouse_id"] = bodega.id

            messages.success(
                request,
                f"¡Bodega '{bodega.name}' creada exitosamente! Tu sistema ya está listo para operar.",
            )
            # 🚀 LLEGAR AL FINAL DEL FLUJO: Ir al Dashboard CEO
            return redirect("/ceo/")
    else:
        form = WarehouseForm()

    context = {
        "form": form,
        "company": company,
    }
    return render(request, "companies/warehouse_form.html", context)


@login_required
def company_profile(request):
    """
    Vista de perfil corporativo.
    """
    profile = getattr(request.user, "userprofile", None) or getattr(
        request.user, "profile", None
    )
    company = profile.company if profile else None

    if not company:
        return redirect("companies:crear_empresa")

    return render(request, "companies/profile.html", {"company": company})


@login_required
def warehouse_list(request):
    """
    Listado de bodegas registradas por la empresa.
    """
    profile = getattr(request.user, "userprofile", None) or getattr(
        request.user, "profile", None
    )
    company = profile.company if profile else None

    if not company:
        return redirect("companies:crear_empresa")

    warehouses = Warehouse.objects.filter(company=company)
    return render(
        request,
        "companies/warehouse_list.html",
        {"warehouses": warehouses, "company": company},
    )



@login_required(login_url="/admin/login/")
def onboarding_wms360(request):
    """
    Onboarding inicial de WMS360.

    Esta vista es informativa.
    No crea ni modifica empresas, bodegas,
    suscripciones ni permisos.
    """

    return render(
        request,
        "companies/onboarding_wms360.html",
    )
