import logging
import uuid
from datetime import timedelta

import sentry_sdk
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .emails import resolve_recipient
from .models import CustomerEmailNotification as Notice

logger = logging.getLogger(__name__)

MANUAL_EMAIL_COOLDOWN = timedelta(minutes=2)


def customer_emails_enabled():
    return bool(getattr(settings, "CUSTOMER_EMAILS_ENABLED", True))


def enqueue_notification(notice_id):
    """Encola el envío. Si Redis falla, la tarea periódica lo recupera."""
    try:
        from .tasks import send_customer_email_task

        send_customer_email_task.delay(notice_id)
    except Exception as error:  # broker caído: no se pierde, queda pending
        logger.warning("No se pudo encolar el correo %s: %s", notice_id, error)
        sentry_sdk.capture_exception(error)


def queue_order_email(
    order,
    event_type,
    *,
    payment=None,
    dedupe_key=None,
    requested_by=None,
):
    """
    Registra un correo pendiente para el comprador y lo encola al confirmar
    la transacción. Idempotente por (empresa, dedupe_key).

    Devuelve la fila creada, o None si no aplica (apagado, sin correo o ya
    existía).
    """
    if order is None or not order.pk:
        return None
    if not customer_emails_enabled():
        return None
    if not resolve_recipient(order):
        return None

    if dedupe_key is None:
        related = payment.pk if payment is not None else order.pk
        dedupe_key = f"{event_type}:{order.pk}:{related}"

    try:
        with transaction.atomic():
            notice = Notice.objects.create(
                company_id=order.company_id,
                order_id=order.pk,
                payment=payment,
                event_type=event_type,
                dedupe_key=dedupe_key[:120],
                requested_by=requested_by,
            )
    except IntegrityError:
        return None

    notice_id = notice.pk
    transaction.on_commit(lambda: enqueue_notification(notice_id), robust=True)
    return notice


def queue_manual_email(order, user):
    """
    Correo manual desde el detalle del pedido, con freno anti doble clic.
    Devuelve (notice|None, motivo).
    """
    if not customer_emails_enabled():
        return None, "disabled"
    if not resolve_recipient(order):
        return None, "no_email"

    recent = Notice.objects.filter(
        company_id=order.company_id,
        order_id=order.pk,
        event_type=Notice.EVENT_MANUAL,
        created_at__gte=timezone.now() - MANUAL_EMAIL_COOLDOWN,
    ).exists()
    if recent:
        return None, "cooldown"

    notice = queue_order_email(
        order,
        Notice.EVENT_MANUAL,
        dedupe_key=f"manual:{order.pk}:{uuid.uuid4().hex}",
        requested_by=user,
    )
    return notice, "queued" if notice else "skipped"
