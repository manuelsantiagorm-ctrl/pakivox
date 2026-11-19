import logging

import sentry_sdk
from celery import shared_task

from inventory.expiry_services import sync_lot_expiry_alerts

logger = logging.getLogger(__name__)


@shared_task(bind=True, ignore_result=True)
def sync_lot_expiry_alerts_task(self):
    """
    Tarea periodica (Celery Beat): sincroniza alertas de caducidad de
    lotes para TODAS las empresas (sin filtro de company/warehouse).
    Equivalente al comando de management sync_lot_expiry_alerts sin
    argumentos, para correr automaticamente sin intervencion manual.
    """
    try:
        result = sync_lot_expiry_alerts(company=None, warehouse_ids=None, dry_run=False)
    except Exception as error:
        logger.exception("sync_lot_expiry_alerts_task: error inesperado")
        sentry_sdk.capture_exception(error)
        raise

    logger.info(
        "sync_lot_expiry_alerts_task: activas=%s creadas=%s actualizadas=%s resueltas=%s",
        result.get("active"),
        result.get("created"),
        result.get("updated"),
        result.get("resolved"),
    )
    return result
