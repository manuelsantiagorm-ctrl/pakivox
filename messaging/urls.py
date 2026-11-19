from django.urls import include, path
from accounts.capability_access import capability_required

from . import embedded_signup_views
from . import views

app_name = "messaging"


urlpatterns = [
    path(
        "",
        capability_required("messaging.operate")(views.messaging_home),
        name="home",
    ),
    # Canales WhatsApp
    path(
        "canales/",
        capability_required("messaging.configure")(views.channels_list),
        name="channels_list",
    ),
    path(
        "canales/nuevo/",
        capability_required("messaging.configure")(views.channel_create),
        name="channel_create",
    ),
    path(
        "canales/<int:pk>/",
        capability_required("messaging.configure")(views.channel_detail),
        name="channel_detail",
    ),
    path(
        "canales/<int:pk>/agentes/agregar/",
        capability_required("messaging.configure")(views.channel_assign_agent),
        name="channel_assign_agent",
    ),
    path(
        "asignaciones/<int:pk>/desactivar/",
        capability_required("messaging.configure")(views.assignment_deactivate),
        name="assignment_deactivate",
    ),

    path(
        "meta/embedded-signup/",
        capability_required("messaging.configure")(
            embedded_signup_views.meta_embedded_signup_start
        ),
        name="meta_embedded_signup_start",
    ),

    path(
        "meta/embedded-signup/callback/",
        capability_required("messaging.configure")(
            embedded_signup_views.meta_embedded_signup_callback
        ),
        name="meta_embedded_signup_callback",
    ),


    # Bandeja de chats
    path(
        "chats/",
        capability_required("messaging.operate")(views.chat_inbox),
        name="chat_inbox",
    ),
    path(
        "chats/demo/",
        capability_required("messaging.supervise")(views.create_demo_chat),
        name="create_demo_chat",
    ),
    path(
        "chats/<int:pk>/",
        capability_required("messaging.operate")(views.chat_detail),
        name="chat_detail",
    ),
    path(
        "chats/<int:pk>/responder/",
        capability_required("messaging.operate")(views.chat_reply),
        name="chat_reply",
    ),
    path(
        "chats/<int:pk>/asignarme/",
        capability_required("messaging.operate")(views.chat_assign_me),
        name="chat_assign_me",
    ),
    path(
        "chats/<int:pk>/cerrar/",
        capability_required("messaging.operate")(views.chat_close),
        name="chat_close",
    ),
    path(
        "chats/<int:pk>/reabrir/",
        capability_required("messaging.operate")(views.chat_reopen),
        name="chat_reopen",
    ),
    # Pedido Express desde chat
    path(
        "webhooks/twilio/whatsapp/",
        views.twilio_inbound_webhook,
        name="twilio_inbound_webhook",
    ),
    path(
        "webhooks/meta/whatsapp/",
        views.meta_whatsapp_webhook,
        name="meta_whatsapp_webhook",
    ),
    path("webhooks/ycloud/whatsapp/", views.ycloud_whatsapp_webhook, name="ycloud_whatsapp_webhook"),
    path(
        "chats/<int:pk>/crear-pedido/",
        capability_required("messaging.operate")(views.chat_create_order),
        name="chat_create_order",
    ),
    path("owner/", views.owner_saas_console, name="owner_saas_console"),
    path(
        "owner/ycloud/<int:pk>/verificar/",
        views.owner_ycloud_verify_channel,
        name="owner_ycloud_verify_channel",
    ),
    path(
        "owner/notificaciones/",
        views.owner_notification_logs,
        name="owner_notification_logs",
    ),
    path(
        "ia/borradores/",
        capability_required("messaging.operate")(views.ai_offer_draft_list),
        name="ai_offer_draft_list",
    ),
    path(
        "ia/borradores/crear/",
        capability_required("messaging.operate")(views.ai_offer_draft_create),
        name="ai_offer_draft_create",
    ),
    path(
        "ia/borradores/<int:pk>/",
        capability_required("messaging.operate")(views.ai_offer_draft_detail),
        name="ai_offer_draft_detail",
    ),
    path("owner/onboarding/", include("messaging.onboarding_urls")),
    path("health/", include("messaging.production_urls")),
]


