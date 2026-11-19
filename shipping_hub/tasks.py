import logging

import sentry_sdk
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, ignore_result=True)
def sync_shipment_batch_alerts_task(self):
    """
    Tarea periódica (Celery Beat): recorre todos los lotes de recolección
    abiertos y sincroniza su notificación escalonada (12h/24h/48h).

    No cambia el estado del lote -- solo mantiene la alerta al día para
    que Camila/Héctor/el dueño se enteren aunque nadie haga ninguna
    acción nueva sobre el lote.
    """
    from notifications.services import sync_shipment_batch_notification
    from shipping_hub.models import ShipmentBatch

    processed = 0
    try:
        batches = ShipmentBatch.objects.filter(status=ShipmentBatch.STATUS_OPEN)
        for batch in batches:
            sync_shipment_batch_notification(batch)
            processed += 1
    except Exception as error:
        logger.exception("sync_shipment_batch_alerts_task: error inesperado")
        sentry_sdk.capture_exception(error)
        raise

    logger.info("sync_shipment_batch_alerts_task: %s lote(s) procesado(s)", processed)
    return {"processed": processed}


@shared_task(bind=True, ignore_result=True)
def sync_shipping_incident_alerts_task(self):
    """
    Tarea periódica (Celery Beat): recorre todas las incidencias
    logísticas abiertas/en investigación y sincroniza su notificación
    escalonada (info -> 24h warning -> 48h critical).
    """
    from notifications.services import sync_shipping_incident_notification
    from shipping_hub.models import LogisticsIncident

    processed = 0
    try:
        incidents = LogisticsIncident.objects.filter(
            status__in=[
                LogisticsIncident.STATUS_OPEN,
                LogisticsIncident.STATUS_INVESTIGATING,
            ]
        )
        for incident in incidents:
            sync_shipping_incident_notification(incident)
            processed += 1
    except Exception as error:
        logger.exception("sync_shipping_incident_alerts_task: error inesperado")
        sentry_sdk.capture_exception(error)
        raise

    logger.info(
        "sync_shipping_incident_alerts_task: %s incidencia(s) procesada(s)", processed
    )
    return {"processed": processed}
