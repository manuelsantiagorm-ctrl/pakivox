import logging
from datetime import timedelta

import sentry_sdk
from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from messaging.models import Message, WebhookEvent

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 365
DEFAULT_BATCH_SIZE = 500


@shared_task(bind=True, ignore_result=True)
def purge_old_whatsapp_payloads_task(
    self, retention_days=DEFAULT_RETENTION_DAYS, batch_size=DEFAULT_BATCH_SIZE
):
    """
    Tarea periodica (Celery Beat): BUG-067, anonimiza payloads de
    mensajes y webhooks de WhatsApp mas antiguos que `retention_days`.

    IMPORTANTE: a diferencia del comando de management
    purge_old_whatsapp_payloads (que por defecto es dry-run y requiere
    --apply), esta tarea SIEMPRE aplica los cambios de verdad. Es la
    version pensada para correr sola en produccion via Celery Beat -
    si no aplicara de verdad, el "cumplimiento legal automatico" no
    haria nada en realidad.
    """
    try:
        cutoff = timezone.now() - timedelta(days=retention_days)
        purged_marker = {
            "purged": True,
            "purged_at": timezone.now().isoformat(),
            "reason": (
                f"Retencion de {retention_days} dias expirada "
                f"(BUG-067, tarea automatica)."
            ),
        }

        messages_to_purge = Message.objects.filter(created_at__lt=cutoff).filter(
            Q(raw_payload__purged__isnull=True) | ~Q(raw_payload__purged=True)
        )
        webhooks_to_purge = WebhookEvent.objects.filter(received_at__lt=cutoff).filter(
            Q(payload__purged__isnull=True) | ~Q(payload__purged=True)
        )

        total_messages = 0
        while True:
            batch_ids = list(messages_to_purge.values_list("pk", flat=True)[:batch_size])
            if not batch_ids:
                break
            with transaction.atomic():
                Message.objects.filter(pk__in=batch_ids).update(raw_payload=purged_marker)
            total_messages += len(batch_ids)

        total_webhooks = 0
        while True:
            batch_ids = list(webhooks_to_purge.values_list("pk", flat=True)[:batch_size])
            if not batch_ids:
                break
            with transaction.atomic():
                WebhookEvent.objects.filter(pk__in=batch_ids).update(payload=purged_marker)
            total_webhooks += len(batch_ids)

    except Exception as error:
        logger.exception("purge_old_whatsapp_payloads_task: error inesperado")
        sentry_sdk.capture_exception(error)
        raise

    logger.info(
        "purge_old_whatsapp_payloads_task: mensajes=%s webhooks=%s",
        total_messages,
        total_webhooks,
    )
    return {"messages_purged": total_messages, "webhooks_purged": total_webhooks}

@shared_task(bind=True, ignore_result=True, max_retries=3, default_retry_delay=30)
def notificar_pago_aprobado_whatsapp_task(self, payment_id):
    """
    Envia en segundo plano la notificacion de WhatsApp de pago
    aprobado. Se movio a Celery porque send_whatsapp_text() hace una
    llamada HTTP real al proveedor y no debe bloquear el
    request/response del usuario.
    """
    from payments.models import Payment
    from messaging.services import notificar_pago_aprobado_whatsapp

    try:
        payment = Payment.objects.select_related(
            "company",
            "order",
            "order__company",
            "order__customer",
        ).get(pk=payment_id)
    except Payment.DoesNotExist:
        logger.warning(
            "notificar_pago_aprobado_whatsapp_task: Payment %s ya no existe",
            payment_id,
        )
        return None

    try:
        notificar_pago_aprobado_whatsapp(payment)
    except Exception as error:
        logger.exception("notificar_pago_aprobado_whatsapp_task: error inesperado")
        sentry_sdk.capture_exception(error)
        raise self.retry(exc=error)


@shared_task(bind=True, ignore_result=True, max_retries=3, default_retry_delay=30)
def notificar_pedido_enviado_whatsapp_task(self, shipping_order_id, company_id=None):
    """
    Envia en segundo plano la notificacion de WhatsApp de pedido
    enviado. Mismo motivo que notificar_pago_aprobado_whatsapp_task.
    """
    from warehouse.models import ShippingOrder
    from messaging.services import notificar_pedido_enviado_whatsapp

    filters = {"pk": shipping_order_id}
    if company_id is not None:
        filters["company_id"] = company_id

    try:
        shipping_order = ShippingOrder.objects.select_related(
            "order",
            "order__company",
            "order__customer",
        ).get(**filters)
    except ShippingOrder.DoesNotExist:
        logger.warning(
            "notificar_pedido_enviado_whatsapp_task: ShippingOrder %s ya no existe",
            shipping_order_id,
        )
        return None

    try:
        notificar_pedido_enviado_whatsapp(shipping_order)
    except Exception as error:
        logger.exception("notificar_pedido_enviado_whatsapp_task: error inesperado")
        sentry_sdk.capture_exception(error)
        raise self.retry(exc=error)

