"""
Disparadores de los correos al comprador.

Todos los cambios de estado de un pedido pasan por Order.save() (el
escáner de escrituras directas de shipping_hub lo vigila), así que basta
con observar el modelo. El correo de pago aprobado sale del Payment.
"""

import sentry_sdk
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from payments.models import Payment
from sales.models import Order

from .models import CustomerEmailNotification as Notice
from .services import customer_emails_enabled, queue_order_email

_TRACKED_FIELD = "status"


def _touches_status(kwargs):
    update_fields = kwargs.get("update_fields")
    return update_fields is None or _TRACKED_FIELD in update_fields


@receiver(pre_save, sender=Order, dispatch_uid="customer_updates_order_pre_save")
def remember_previous_order_status(sender, instance, raw=False, **kwargs):
    if raw or not customer_emails_enabled() or not _touches_status(kwargs):
        instance._cu_previous_status = getattr(instance, "_cu_previous_status", None)
        instance._cu_status_checked = False
        return
    instance._cu_status_checked = True
    if not instance.pk:
        instance._cu_previous_status = None
        return
    instance._cu_previous_status = (
        Order.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
    )


def order_events_for_transition(previous, current, created):
    """Eventos de correo que genera un cambio de estado del pedido."""
    if current in {"draft", "cancelled"} or (previous == current and not created):
        return []
    events = []
    if previous in (None, "draft"):
        events.append(Notice.EVENT_ORDER_CONFIRMED)
    if current == "shipped":
        events.append(Notice.EVENT_ORDER_SHIPPED)
    if current == "delivered":
        events.append(Notice.EVENT_ORDER_DELIVERED)
    return events


@receiver(post_save, sender=Order, dispatch_uid="customer_updates_order_post_save")
def order_status_customer_emails(sender, instance, created, raw=False, **kwargs):
    if raw or not getattr(instance, "_cu_status_checked", False):
        return
    previous = getattr(instance, "_cu_previous_status", None)
    current = instance.status
    if previous == current and not created:
        return
    instance._cu_previous_status = current

    try:
        for event in order_events_for_transition(previous, current, created):
            queue_order_email(instance, event, dedupe_key=f"{event}:{instance.pk}")
    except Exception as error:  # nunca romper el guardado del pedido
        sentry_sdk.capture_exception(error)


@receiver(post_save, sender=Payment, dispatch_uid="customer_updates_payment_approved")
def payment_approved_customer_email(sender, instance, created, raw=False, **kwargs):
    if raw or instance.status != "approved" or not customer_emails_enabled():
        return
    # payments.signals guarda el estado anterior en pre_save.
    previous = getattr(instance, "_previous_status", None)
    if previous == "approved":
        return
    try:
        order = instance.order
        if order is None:
            return
        queue_order_email(
            order,
            Notice.EVENT_PAYMENT_APPROVED,
            payment=instance,
            dedupe_key=f"{Notice.EVENT_PAYMENT_APPROVED}:{instance.pk}",
        )
    except Exception as error:
        sentry_sdk.capture_exception(error)
