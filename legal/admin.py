from django.contrib import admin
from accounts.capability_access import get_active_user_profile
from legal.models import LegalAcceptance


@admin.register(LegalAcceptance)
class LegalAcceptanceAdmin(admin.ModelAdmin):
    list_display = (
        "document_slug",
        "document_version",
        "company",
        "user",
        "accepted_at",
    )
    list_filter = ("document_slug", "document_version", "purpose")
    search_fields = ("company__name", "user__email", "ip_address")
    readonly_fields = [field.name for field in LegalAcceptance._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        # TRANS-006: aunque no se puede editar/borrar, sin este filtro
        # cualquier usuario con acceso al Admin podía LEER aceptaciones
        # legales de otras empresas del SaaS en el listado.
        queryset = super().get_queryset(request)

        if request.user.is_superuser:
            return queryset

        profile = get_active_user_profile(request.user)

        if not profile or not profile.company_id:
            return queryset.none()

        return queryset.filter(company_id=profile.company_id)
