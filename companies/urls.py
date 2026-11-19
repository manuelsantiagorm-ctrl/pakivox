from django.urls import path
from accounts.capability_access import capability_required

from . import media_views
from . import onboarding_views
from . import profile_views
from . import setup_views

from .warehouse_views import (
    warehouse_create,
    warehouse_list,
    warehouse_toggle_active,
    warehouse_update,
)

app_name = "companies"

urlpatterns = [
    path(
        "perfil/",
        profile_views.company_profile,
        name="profile",
    ),
    path(
        "perfil/editar/",
        capability_required("company.configure")(profile_views.company_profile_edit),
        name="profile_edit",
    ),
    path(
        "perfil/logo/subir/",
        capability_required("company.configure")(media_views.company_logo_upload),
        name="logo_upload",
    ),
    path(
        "perfil/logo/archivo/",
        media_views.company_logo_file,
        name="logo_file",
    ),
    path(
        "perfil/fotos/subir/",
        capability_required("company.configure")(media_views.company_branch_photo_upload),
        name="branch_photo_upload",
    ),
    path(
        "perfil/fotos/<int:photo_id>/archivo/",
        media_views.company_branch_photo_file,
        name="branch_photo_file",
    ),
    path(
        "perfil/fotos/<int:photo_id>/eliminar/",
        capability_required("company.configure")(media_views.company_branch_photo_delete),
        name="branch_photo_delete",
    ),
    path(
        "perfil/documentos/subir/",
        capability_required("company.configure")(media_views.company_document_upload),
        name="document_upload",
    ),
    path(
        "perfil/documentos/<int:document_id>/archivo/",
        media_views.company_document_file,
        name="document_file",
    ),
    path(
        "perfil/documentos/<int:document_id>/eliminar/",
        capability_required("company.configure")(media_views.company_document_delete),
        name="document_delete",
    ),
    path(
        "configuracion-inicial/",
        setup_views.initial_setup,
        name="initial_setup",
    ),
    path(
        "bienvenida/",
        onboarding_views.onboarding_wms360,
        name="onboarding_wms360",
    ),
    path(
        "bodegas/",
        capability_required("company.configure")(warehouse_list),
        name="warehouse_list",
    ),
    path(
        "bodegas/nueva/",
        capability_required("company.configure")(warehouse_create),
        name="warehouse_create",
    ),
    path(
        "bodegas/<int:warehouse_id>/editar/",
        capability_required("company.configure")(warehouse_update),
        name="warehouse_update",
    ),
    path(
        "bodegas/<int:warehouse_id>/cambiar-estado/",
        capability_required("company.configure")(warehouse_toggle_active),
        name="warehouse_toggle_active",
    ),
]
