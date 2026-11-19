from django.contrib import admin

from .models import CustomerEmailNotification, OrderTrackingLink, WhatsAppDraftLog


class _ReadOnlyCompanyAdmin(admin.ModelAdmin):
    """Bitácoras de solo lectura, filtradas por empresa para no superusuarios."""

    def get_queryset(self, request):
        from companies.scoping import get_request_company

        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        company = get_request_company(request)
        if company is None:
            return queryset.none()
        return queryset.filter(company=company)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(OrderTrackingLink)
class OrderTrackingLinkAdmin(_ReadOnlyCompanyAdmin):
    list_display = ("order", "company", "view_count", "last_viewed_at", "created_at", "rotated_at")
    list_select_related = ("order", "company")
    raw_id_fields = ("order",)
    # El token no se muestra: es la llave del link público.
    exclude = ("token",)


@admin.register(CustomerEmailNotification)
class CustomerEmailNotificationAdmin(_ReadOnlyCompanyAdmin):
    list_display = ("order", "company", "event_type", "status", "recipient", "attempts", "created_at", "sent_at")
    list_filter = ("status", "event_type")
    list_select_related = ("order", "company")
    search_fields = ("order__folio", "recipient")
    raw_id_fields = ("order", "payment", "requested_by")


@admin.register(WhatsAppDraftLog)
class WhatsAppDraftLogAdmin(_ReadOnlyCompanyAdmin):
    list_display = ("order", "company", "draft_key", "phone", "phone_source", "was_edited", "created_by", "created_at")
    list_filter = ("draft_key", "phone_source")
    list_select_related = ("order", "company", "created_by")
    raw_id_fields = ("order", "created_by")
