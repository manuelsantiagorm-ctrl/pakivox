from django.urls import path

from . import views
from . import webhooks

app_name = "subscriptions"

urlpatterns = [
    path("vencida/", views.subscription_expired, name="subscription_expired"),
    path(
        "stripe/checkout/",
        views.create_stripe_checkout_session,
        name="create_checkout_session",
    ),
    path(
        "stripe/success/",
        views.checkout_success,
        name="checkout_success",
    ),
    path(
        "whatsapp/autorizar-excedente/",
        views.authorize_whatsapp_overage,
        name="authorize_whatsapp_overage",
    ),
    # Ruta oficial
    path(
        "stripe/webhook/",
        webhooks.stripe_webhook,
        name="stripe_webhook",
    ),
    # Ruta extra para evitar 404 si Stripe CLI apunta a /subscriptions/webhook/
    path(
        "webhook/",
        webhooks.stripe_webhook,
        name="stripe_webhook_legacy",
    ),
]
