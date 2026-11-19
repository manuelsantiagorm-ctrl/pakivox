import mimetypes
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import (
    FileResponse,
    Http404,
    HttpResponseForbidden,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .media_forms import (
    CompanyBranchPhotoForm,
    CompanyDocumentForm,
    CompanyLogoUploadForm,
)
from .media_models import (
    CompanyBranchPhoto,
    CompanyBranding,
    CompanyDocument,
)


LOGO_EDIT_ROLES = {
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
PHOTO_DELETE_ROLES = {
    "owner",
    "admin",
    "manager",
    "warehouse",
}


def get_active_profile(request):
    profile = getattr(request.user, "profile", None)

    if not profile:
        return None

    if not profile.is_active:
        return None

    if not profile.company.is_active:
        return None

    return profile


def puede_ver_documentos(user, profile):
    """El expediente (constancia fiscal, identificaciones, contratos) es
    información sensible: solo lo ve quien lo administra o quien lleva la
    contabilidad. Ser trabajador de la empresa no basta."""
    from accounts.capability_access import user_has_capability

    if profile.role in DOCUMENT_EDIT_ROLES:
        return True
    return user_has_capability(user, "company.configure") or user_has_capability(
        user, "accounting.view"
    )


def require_profile(request):
    profile = get_active_profile(request)

    if not profile:
        raise Http404(
            "No existe una empresa activa para este usuario."
        )

    return profile


def redirect_to_media_tab():
    return redirect(
        f"{reverse('companies:profile')}?tab=media"
    )


def file_response(field_file, download=False):
    if not field_file:
        raise Http404("Archivo no disponible.")

    try:
        file_handle = field_file.open("rb")
    except (FileNotFoundError, OSError, ValueError):
        raise Http404("El archivo físico no está disponible.")

    filename = Path(field_file.name).name
    content_type = (
        mimetypes.guess_type(filename)[0]
        or "application/octet-stream"
    )

    return FileResponse(
        file_handle,
        as_attachment=download,
        filename=filename,
        content_type=content_type,
    )


@login_required
@require_POST
@transaction.atomic
def company_logo_upload(request):
    profile = require_profile(request)

    if profile.role not in LOGO_EDIT_ROLES:
        return JsonResponse(
            {
                "ok": False,
                "error": "Tu rol no permite cambiar el logo.",
            },
            status=403,
        )

    form = CompanyLogoUploadForm(
        request.POST,
        request.FILES,
    )

    if not form.is_valid():
        return JsonResponse(
            {
                "ok": False,
                "error": " ".join(
                    error
                    for errors in form.errors.values()
                    for error in errors
                ),
            },
            status=400,
        )

    branding, _ = CompanyBranding.objects.select_for_update().get_or_create(
        company=profile.company,
    )

    old_logo = branding.logo
    branding.logo = form.cleaned_data["logo"]
    branding.updated_by = request.user
    branding.save()

    if old_logo and old_logo.name != branding.logo.name:
        old_logo.delete(save=False)

    return JsonResponse(
        {
            "ok": True,
            "message": "Logo actualizado correctamente.",
            "logo_url": reverse("companies:logo_file"),
        }
    )


@login_required
@require_GET
def company_logo_file(request):
    profile = require_profile(request)
    branding = get_object_or_404(
        CompanyBranding,
        company=profile.company,
    )
    return file_response(branding.logo)


@login_required
@require_POST
def company_branch_photo_upload(request):
    profile = require_profile(request)

    if profile.role not in PHOTO_UPLOAD_ROLES:
        return HttpResponseForbidden(
            "Tu rol no permite subir fotografías."
        )

    form = CompanyBranchPhotoForm(
        request.POST,
        request.FILES,
        company=profile.company,
    )

    if form.is_valid():
        photo = form.save(commit=False)
        photo.company = profile.company
        photo.uploaded_by = request.user
        photo.save()
        messages.success(
            request,
            "La fotografía se agregó a la galería logística.",
        )
    else:
        messages.error(
            request,
            "No se pudo subir la fotografía: "
            + " ".join(
                error
                for errors in form.errors.values()
                for error in errors
            ),
        )

    return redirect_to_media_tab()


@login_required
@require_GET
def company_branch_photo_file(request, photo_id):
    profile = require_profile(request)
    photo = get_object_or_404(
        CompanyBranchPhoto,
        pk=photo_id,
        company=profile.company,
    )
    return file_response(photo.image)


@login_required
@require_POST
def company_branch_photo_delete(request, photo_id):
    profile = require_profile(request)

    if profile.role not in PHOTO_DELETE_ROLES:
        return HttpResponseForbidden(
            "Tu rol no permite eliminar fotografías."
        )

    photo = get_object_or_404(
        CompanyBranchPhoto,
        pk=photo_id,
        company=profile.company,
    )
    stored_file = photo.image
    photo.delete()

    if stored_file:
        stored_file.delete(save=False)

    messages.success(
        request,
        "La fotografía fue eliminada.",
    )
    return redirect_to_media_tab()


@login_required
@require_POST
def company_document_upload(request):
    profile = require_profile(request)

    if profile.role not in DOCUMENT_EDIT_ROLES:
        return HttpResponseForbidden(
            "Tu rol no permite administrar documentos fiscales."
        )

    form = CompanyDocumentForm(
        request.POST,
        request.FILES,
    )

    if form.is_valid():
        document = form.save(commit=False)
        document.company = profile.company
        document.uploaded_by = request.user
        document.save()
        messages.success(
            request,
            "Documento PDF guardado correctamente.",
        )
    else:
        messages.error(
            request,
            "No se pudo guardar el documento: "
            + " ".join(
                error
                for errors in form.errors.values()
                for error in errors
            ),
        )

    return redirect_to_media_tab()


@login_required
@require_GET
def company_document_file(request, document_id):
    profile = require_profile(request)
    if not puede_ver_documentos(request.user, profile):
        return HttpResponseForbidden(
            "Tu acceso no incluye los documentos de la empresa."
        )
    document = get_object_or_404(
        CompanyDocument,
        pk=document_id,
        company=profile.company,
    )
    download = request.GET.get("download") == "1"
    return file_response(
        document.file,
        download=download,
    )


@login_required
@require_POST
def company_document_delete(request, document_id):
    profile = require_profile(request)

    if profile.role not in DOCUMENT_EDIT_ROLES:
        return HttpResponseForbidden(
            "Tu rol no permite eliminar documentos fiscales."
        )

    document = get_object_or_404(
        CompanyDocument,
        pk=document_id,
        company=profile.company,
    )
    stored_file = document.file
    document.delete()

    if stored_file:
        stored_file.delete(save=False)

    messages.success(
        request,
        "El documento fue eliminado.",
    )
    return redirect_to_media_tab()
