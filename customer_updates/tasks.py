import logging
from datetime import timedelta

import sentry_sdk
from celery import shared_task
from django.db.models import F, Q
from django.utils import timezone

from .models import CustomerEmailNotification as Notice

logger = logging.getLogger(__name__)

# Si un worker murió a media entrega, la fila se puede reclamar otra vez.
STALE_CLAIM_AFTER = timedelta(minutes=10)
# Un pendiente que nadie tomó en este tiempo se vuelve a encolar.
PENDING_GRACE = timedelta(minutes=2)
SWEEP_BATCH = 500


def _claim(notice_id):
    now = timezone.now()
    claimable = Q(status__in=[Notice.STATUS_PENDING, Notice.STATUS_FAILED]) | Q(
        status=Notice.STATUS_SENDING,
        claimed_at__lt=now - STALE_CLAIM_AFTER,
    )
    return (
        Notice.objects.filter(pk=notice_id, attempts__lt=Notice.MAX_ATTEMPTS)
        .filter(claimable)
        .update(
            status=Notice.STATUS_SENDING,
            claimed_at=now,
            attempts=F("attempts") + 1,
            updated_at=now,
        )
    )


def deliver_notification(notice_id):
    """
    Entrega un correo una sola vez aunque la tarea se repita.

    La fila se "reclama" con un UPDATE condicional (atómico en PostgreSQL),
    así dos workers nunca envían el mismo aviso, y el SMTP corre fuera de
    cualquier transacción para no tener filas bloqueadas mientras tanto.
    """
    from .emails import build_message, resolve_recipient, send_message

    if not _claim(notice_id):
        return "skipped:not-claimable"

    notice = Notice.objects.select_related(
        "order",
        "order__company",
        "order__customer",
        "payment",
    ).get(pk=notice_id)
    order = notice.order

    if not order.company.is_active:
        Notice.objects.filter(pk=notice_id).update(
            status=Notice.STATUS_SKIPPED,
            last_error="Empresa inactiva.",
            updated_at=timezone.now(),
        )
        return "skipped:inactive-company"

    recipient = resolve_recipient(order)
    if not recipient:
        Notice.objects.filter(pk=notice_id).update(
            status=Notice.STATUS_SKIPPED,
            last_error="El cliente no tiene un correo válido.",
            updated_at=timezone.now(),
        )
        return "skipped:no-recipient"

    try:
        subject, text_body, html_body, _ = build_message(notice, order, notice.payment)
        send_message(
            order=order,
            recipient=recipient,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )
    except Exception as error:
        logger.warning("Correo al cliente %s falló: %s", notice_id, error)
        sentry_sdk.capture_exception(error)
        Notice.objects.filter(pk=notice_id).update(
            status=Notice.STATUS_FAILED,
            recipient=recipient,
            last_error=str(error)[:500],
            updated_at=timezone.now(),
        )
        return "failed"

    now = timezone.now()
    Notice.objects.filter(pk=notice_id).update(
        status=Notice.STATUS_SENT,
        recipient=recipient,
        subject=subject,
        sent_at=now,
        last_error="",
        updated_at=now,
    )
    return "sent"


@shared_task(bind=True, ignore_result=True, acks_late=True)
def send_customer_email_task(self, notice_id):
    try:
        return deliver_notification(notice_id)
    except Notice.DoesNotExist:
        return "skipped:missing"


@shared_task(bind=True, ignore_result=True)
def sweep_customer_emails_task(self, batch_size=SWEEP_BATCH):
    """
    Cada 10 minutos (Celery Beat): vuelve a encolar avisos que se quedaron
    sin enviar (Redis caído al encolar, worker reiniciado o SMTP con falla
    temporal). Los fallidos esperan más entre intentos: 10, 20, 30... min.
    """
    now = timezone.now()
    candidates = (
        Notice.objects.filter(attempts__lt=Notice.MAX_ATTEMPTS)
        .filter(
            Q(status=Notice.STATUS_PENDING, updated_at__lt=now - PENDING_GRACE)
            | Q(status=Notice.STATUS_FAILED)
            | Q(status=Notice.STATUS_SENDING, claimed_at__lt=now - STALE_CLAIM_AFTER)
        )
        .order_by("updated_at")
        .values_list("pk", "status", "attempts", "updated_at")[:batch_size]
    )
    queued = 0
    for pk, status, attempts, updated_at in candidates:
        if status == Notice.STATUS_FAILED and updated_at > now - timedelta(minutes=10 * max(attempts, 1)):
            continue
        send_customer_email_task.delay(pk)
        queued += 1
    return queued
