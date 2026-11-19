"""
Tareas periodicas de suscripciones.

PLANES 2.4b - Reenvio automatico de los WhatsApp detenidos por el tope.

Cuando una empresa llega a su tope mensual, los mensajes no se pierden:
quedan guardados en WhatsAppPendingMessage esperando a que el dueno
autorice el excedente. Pero hay un segundo camino, el que resuelve esta
tarea: cuando el periodo se renueva llega cupo fresco, y esa cola debe
salir sola, sin autorizacion y sin cobro de excedente.

Por que una tarea periodica y no un gancho en el codigo de cuota:

  - reset_whatsapp_period_if_needed() corre dentro de una transaccion con
    el registro de suscripcion bloqueado. Meter llamadas de red a Meta
    ahi significaria mantener ese candado abierto mientras se habla con
    un servidor externo.

  - Esa funcion solo se ejecuta cuando alguien manda un mensaje. Una
    empresa con la cola llena y sin trafico nuevo nunca la drenaria, que
    es justo el caso que queremos resolver.

Corre cada hora, no cada dia. Los mensajes detenidos son transaccionales
("tu pedido va en camino"); un dia de retraso los vuelve inutiles.
"""

import logging

import sentry_sdk
from celery import shared_task
from django.db import transaction

logger = logging.getLogger(__name__)


@shared_task(bind=True, ignore_result=True)
def drain_renewed_whatsapp_queues_task(self):
    """
    Revisa las empresas que tienen mensajes detenidos y drena las que ya
    tienen cupo disponible.

    La regla de disparo no es "cambio de periodo" sino "hay cupo". Es una
    regla mas amplia a proposito: cubre la renovacion mensual, pero
    tambien cubre que el dueno se cambie a un plan mas grande. En los dos
    casos lo que el cliente quiere es que sus mensajes salgan.

    Nunca drena mas mensajes de los que caben en el cupo libre. Esto es
    critico: drain_whatsapp_pending_queue() saca el mensaje de la cola
    ANTES de llamar al proveedor, asi que un mensaje que se topa con el
    bloqueo de cuota queda marcado como fallido y ya no vuelve a la cola.
    Si aqui pidieramos drenar 50 mensajes con 10 de cupo, los 40
    restantes se perderian. Por eso se pasa max_messages.

    Una empresa que falla no detiene a las demas: cada una va en su
    propio try, y el error se reporta a Sentry.
    """
    from messaging.models import WhatsAppPendingMessage
    from subscriptions.models import Subscription
    from subscriptions.whatsapp_queue import drain_whatsapp_pending_queue
    from subscriptions.whatsapp_quota import reset_whatsapp_period_if_needed

    company_ids = list(
        WhatsAppPendingMessage.objects.values_list(
            "company_id", flat=True
        ).distinct()
    )

    if not company_ids:
        return {"empresas": 0, "enviados": 0, "fallidos": 0}

    companies_drained = 0
    total_sent = 0
    total_failed = 0

    for company_id in company_ids:
        try:
            subscription = (
                Subscription.objects.select_related("plan", "company")
                .filter(company_id=company_id)
                .order_by("-id")
                .first()
            )

            if not subscription:
                continue

            # El cierre de periodo se asienta con el registro bloqueado,
            # igual que en el resto del flujo de cuota, para que dos
            # procesos simultaneos no lo apliquen dos veces. Dentro de
            # este bloque no se hace ninguna llamada de red.
            with transaction.atomic():
                locked = (
                    Subscription.objects.select_for_update()
                    .select_related("plan")
                    .filter(pk=subscription.pk)
                    .first()
                )

                if not locked:
                    continue

                reset_whatsapp_period_if_needed(locked)
                locked.refresh_from_db()

                plan = getattr(locked, "plan", None)
                limit = (
                    getattr(plan, "max_whatsapp_messages_per_month", None)
                    if plan
                    else None
                )
                used = getattr(locked, "whatsapp_messages_used", 0) or 0

            if limit is None:
                # Sin tope: nada que racionar.
                allowance = None
            else:
                allowance = max(int(limit) - int(used), 0)

                if allowance == 0:
                    # Sigue topada. La cola se queda donde esta,
                    # esperando autorizacion o la proxima renovacion.
                    continue

            sent, failed = drain_whatsapp_pending_queue(
                subscription.company,
                max_messages=allowance,
            )

            if sent or failed:
                companies_drained += 1
                total_sent += sent
                total_failed += failed

                logger.info(
                    "WhatsApp detenidos reenviados tras renovacion "
                    "(empresa=%s, enviados=%s, fallidos=%s)",
                    company_id,
                    sent,
                    failed,
                )

        except Exception as error:  # noqa: BLE001
            sentry_sdk.capture_exception(error)
            logger.exception(
                "Fallo al drenar la cola de WhatsApp de la empresa %s",
                company_id,
            )

    return {
        "empresas": companies_drained,
        "enviados": total_sent,
        "fallidos": total_failed,
    }
