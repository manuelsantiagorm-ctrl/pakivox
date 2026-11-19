from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import (
    redirect,
    render,
)

from companies.models import Company

from .profile_forms import CompanyProfileForm
from .media_forms import (
    CompanyBranchPhotoForm,
    CompanyDocumentForm,
    CompanyLogoUploadForm,
)
from .media_views import puede_ver_documentos
from .profile_services import get_company_profile_context


EDIT_ROLES = {
    "owner",
    "admin",
}

PHOTO_UPLOAD_ROLES = {
    "owner",
    "admin",
    "manager",
    "sales",
    "warehouse",
    "accounting",
}
DOCUMENT_EDIT_ROLES = {
    "owner",
    "admin",
    "accounting",
}


def get_active_profile(
    request,
):
    profile = getattr(
        request.user,
        "profile",
        None,
    )

    if not profile:
        return None

    if not profile.is_active:
        return None

    if not profile.company.is_active:
        return None

    return profile


@login_required
def company_profile(
    request,
):
    profile = get_active_profile(
        request
    )

    if not profile:
        if request.user.is_superuser:
            return redirect(
                "owner:console"
            )

        return HttpResponseForbidden(
            "Tu usuario no tiene una empresa activa asignada."
        )

    context = get_company_profile_context(
        profile.company
    )

    context.update(
        {
            "profile": profile,
            "can_edit_company": (
                profile.role
                in EDIT_ROLES
            ),
            "can_upload_logo": (
                profile.role
                in EDIT_ROLES
            ),
            "can_upload_photos": (
                profile.role
                in PHOTO_UPLOAD_ROLES
            ),
            "can_upload_documents": (
                profile.role
                in DOCUMENT_EDIT_ROLES
            ),
            "can_view_documents": puede_ver_documentos(
                request.user, profile
            ),
            "can_delete_photos": (
                profile.role
                in {
                    "owner",
                    "admin",
                    "manager",
                    "warehouse",
                }
            ),
            "logo_form": CompanyLogoUploadForm(),
            "photo_form": CompanyBranchPhotoForm(
                company=profile.company,
            ),
            "document_form": CompanyDocumentForm(),
        }
    )

    if not context["can_view_documents"]:
        # Ni siquiera los títulos del expediente llegan a la plantilla.
        context["company_documents"] = []

    return render(
        request,
        "companies/company_profile.html",
        context,
    )


@login_required
@transaction.atomic
def company_profile_edit(
    request,
):
    profile = get_active_profile(request)

    if not profile:
        return HttpResponseForbidden("Tu usuario no tiene una empresa activa asignada.")

    if profile.role not in EDIT_ROLES:
        return HttpResponseForbidden(
            "Tu rol no permite modificar la información corporativa."
        )

    company = Company.objects.select_for_update().get(
        pk=profile.company_id,
    )

    if request.method == "POST":
        form = CompanyProfileForm(
            request.POST,
            instance=company,
            actor=request.user,
        )

        if form.is_valid():
            form.save()

            messages.success(
                request,
                "¡Empresa guardada con éxito! Ahora registra tu primera bodega para comenzar a operar.",
            )

            # 🚀 ENVIAR AL PASO 2 DEL FLUJO: Crear la primera bodega
            return redirect("companies:warehouse_create")

        messages.error(
            request,
            "Revisa los campos marcados antes de guardar.",
        )

    else:
        form = CompanyProfileForm(
            instance=company,
            actor=request.user,
        )

    return render(
        request,
        "companies/company_profile_edit.html",
        {
            "company": company,
            "profile": profile,
            "form": form,
        },
    )
