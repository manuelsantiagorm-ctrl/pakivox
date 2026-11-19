import sentry_sdk
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Payment


@receiver(pre_save, sender=Payment)
def payment_store_previous_status(sender, instance, **kwargs):
    """
    Guarda el status anterior antes de guardar.
    Así sabemos si realmente cambió a approved.
    """

    if not instance.pk:
        instance._previous_status = None
        return

    previous_status = (
        Payment.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
    )

    instance._previous_status = previous_status


@receiver(post_save, sender=Payment)
def payment_approved_whatsapp_notification(sender, instance, created, **kwargs):
    """
    Hito A:
    Si un pago pasa de pending/under_review a approved,
    dispara notificación automática por WhatsApp después del commit.
    """

    previous_status = getattr(instance, "_previous_status", None)

    changed_to_approved = (
        instance.status == "approved" and previous_status != "approved"
    )

    if not changed_to_approved:
        return

    def notify_after_commit():
        from messaging.tasks import notificar_pago_aprobado_whatsapp_task

        try:
            notificar_pago_aprobado_whatsapp_task.delay(instance.pk)
        except Exception as error:
            sentry_sdk.capture_exception(error)

    transaction.on_commit(notify_after_commit)
