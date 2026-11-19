from django.contrib import admin
from companies.scoping import CompanyScopedAdmin
from .forms import WhatsAppConfigAdminForm, WhatsAppNumberAdminForm

from .models import (
    WhatsAppConfig,
    WhatsAppNumber,
    ChannelAgentAssignment,
    MessagingCustomer,
    Conversation,
    Message,
    WebhookEvent,
)


@admin.register(WhatsAppConfig)
class WhatsAppConfigAdmin(CompanyScopedAdmin):
    form = WhatsAppConfigAdminForm
    list_display = (
        "company",
        "meta_business_id",
        "waba_id",
        "is_active",
        "updated_at",
    )

    list_filter = (
        "is_active",
        "company",
    )

    search_fields = (
        "company__name",
        "meta_business_id",
        "waba_id",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Empresa",
            {
                "fields": (
                    "company",
                    "is_active",
                )
            },
        ),
        (
            "Credenciales de Meta / WhatsApp",
            {
                "fields": (
                    "meta_business_id",
                    "waba_id",
                    "permanent_access_token",
                    "webhook_verify_token",
                )
            },
        ),
        (
            "Auditoría",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("company")


@admin.register(WhatsAppNumber)
class WhatsAppNumberAdmin(CompanyScopedAdmin):

    form = WhatsAppNumberAdminForm
    list_display = (
        "company",
        "channel_name",
        "display_number",
        "phone_number_id",
        "is_active",
        "updated_at",
    )

    list_filter = (
        "is_active",
        "company",
    )

    search_fields = (
        "company__name",
        "channel_name",
        "display_number",
        "phone_number_id",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Empresa",
            {
                "fields": (
                    "company",
                    "is_active",
                )
            },
        ),
        (
            "Línea de WhatsApp",
            {
                "fields": (
                    "channel_name",
                    "display_number",
                    "phone_number_id",
                )
            },
        ),
        (
            "Auditoría",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("company")


@admin.register(ChannelAgentAssignment)
class ChannelAgentAssignmentAdmin(CompanyScopedAdmin):
    list_display = (
        "company",
        "whatsapp_number",
        "agent",
        "can_view",
        "can_reply",
        "can_create_order",
        "is_supervisor",
        "is_active",
        "updated_at",
    )

    list_filter = (
        "company",
        "whatsapp_number",
        "is_active",
        "is_supervisor",
        "can_view",
        "can_reply",
        "can_create_order",
    )

    search_fields = (
        "company__name",
        "whatsapp_number__channel_name",
        "whatsapp_number__display_number",
        "agent__username",
        "agent__first_name",
        "agent__last_name",
        "agent__email",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Canal y agente",
            {
                "fields": (
                    "whatsapp_number",
                    "agent",
                    "is_active",
                )
            },
        ),
        (
            "Permisos del agente",
            {
                "fields": (
                    "can_view",
                    "can_reply",
                    "can_create_order",
                    "is_supervisor",
                )
            },
        ),
        (
            "Empresa y auditoría",
            {
                "fields": (
                    "company",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related(
            "company",
            "whatsapp_number",
            "agent",
        )


@admin.register(MessagingCustomer)
class MessagingCustomerAdmin(CompanyScopedAdmin):
    list_display = (
        "company",
        "name",
        "phone",
        "source",
        "updated_at",
    )

    list_filter = (
        "company",
        "source",
    )

    search_fields = (
        "company__name",
        "name",
        "phone",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Empresa",
            {
                "fields": (
                    "company",
                    "source",
                )
            },
        ),
        (
            "Cliente final",
            {
                "fields": (
                    "name",
                    "phone",
                )
            },
        ),
        (
            "Auditoría",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("company")


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = (
        "company",
        "whatsapp_number",
        "customer",
        "assigned_agent",
        "status",
        "last_message_at",
        "updated_at",
    )

    list_filter = (
        "company",
        "status",
        "whatsapp_number",
    )

    search_fields = (
        "company__name",
        "customer__name",
        "customer__phone",
        "whatsapp_number__channel_name",
        "whatsapp_number__display_number",
        "assigned_agent__username",
        "assigned_agent__email",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
        "last_message_at",
    )

    fieldsets = (
        (
            "Empresa y canal",
            {
                "fields": (
                    "company",
                    "whatsapp_number",
                )
            },
        ),
        (
            "Conversación",
            {
                "fields": (
                    "customer",
                    "assigned_agent",
                    "status",
                    "last_message_at",
                )
            },
        ),
        (
            "Auditoría",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related(
            "company",
            "whatsapp_number",
            "customer",
            "assigned_agent",
        )


@admin.register(Message)
class MessageAdmin(CompanyScopedAdmin):
    list_display = (
        "company",
        "conversation",
        "direction",
        "sender_type",
        "message_type",
        "status",
        "agent",
        "created_at",
    )

    list_filter = (
        "company",
        "whatsapp_number",
        "direction",
        "sender_type",
        "message_type",
        "status",
    )

    search_fields = (
        "company__name",
        "body",
        "provider_message_id",
        "conversation__customer__name",
        "conversation__customer__phone",
        "agent__username",
        "agent__email",
    )

    readonly_fields = (
        "internal_id",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Conversación",
            {
                "fields": (
                    "conversation",
                    "company",
                    "whatsapp_number",
                )
            },
        ),
        (
            "Mensaje",
            {
                "fields": (
                    "internal_id",
                    "provider_message_id",
                    "direction",
                    "sender_type",
                    "agent",
                    "message_type",
                    "body",
                    "media_url",
                    "status",
                    "provider_timestamp",
                )
            },
        ),
        (
            "Payload",
            {
                "fields": ("raw_payload",),
                "classes": ("collapse",),
            },
        ),
        (
            "Auditoría",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related(
            "company",
            "whatsapp_number",
            "conversation",
            "conversation__customer",
            "agent",
        )


@admin.register(WebhookEvent)
class WebhookEventAdmin(CompanyScopedAdmin):
    cñlist_display = (
        "id",
        "company",
        "whatsapp_number",
        "event_type",
        "provider_message_id",
        "is_processed",
        "received_at",
        "processed_at",
    )

    list_filter = (
        "is_processed",
        "event_type",
        "company",
        "whatsapp_number",
    )

    search_fields = (
        "provider_message_id",
        "company__name",
        "whatsapp_number__display_number",
        "whatsapp_number__channel_name",
    )

    readonly_fields = (
        "received_at",
        "processed_at",
    )

    fieldsets = (
        (
            "Identificación",
            {
                "fields": (
                    "company",
                    "whatsapp_number",
                    "event_type",
                    "provider_message_id",
                    "is_processed",
                )
            },
        ),
        (
            "Payload crudo",
            {"fields": ("payload",)},
        ),
        (
            "Errores",
            {"fields": ("error_log",)},
        ),
        (
            "Fechas",
            {
                "fields": (
                    "received_at",
                    "processed_at",
                )
            },
        ),
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related(
            "company",
            "whatsapp_number",
        )


# ==========================================================
# SUBCUENTAS DE TWILIO (Paso 3a)
# ==========================================================
# Solo lectura: se crean y suspenden con el comando twilio_subaccount.
# El Auth Token NUNCA se muestra.
from .twilio_models import TwilioSubaccount  # noqa: E402


@admin.register(TwilioSubaccount)
class TwilioSubaccountAdmin(admin.ModelAdmin):
    list_display = ("company", "friendly_name", "account_sid", "status", "updated_at")
    list_filter = ("status",)
    search_fields = ("friendly_name", "account_sid", "company__name")
    exclude = ("auth_token",)
    readonly_fields = (
        "company",
        "friendly_name",
        "account_sid",
        "status",
        "last_error",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

