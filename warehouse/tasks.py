import logging

import sentry_sdk
from celery import shared_task
from django.core.exceptions import ValidationError

from warehouse.services import liberar_reservas_vencidas

logger = logging.getLogger(__name__)


@shared_task(bind=True, ignore_result=True)
def liberar_reservas_vencidas_task(self, hours=24):
    """
    Tarea periodica (Celery Beat): libera reservas de pickings
    pendientes/en progreso con mas de `hours` horas sin resolverse.
    Equivalente al comando de management liberar_reservas_vencidas,
    pero para correr automaticamente sin intervencion manual.
    """
    try:
        results = liberar_reservas_vencidas(hours=hours, user=None, dry_run=False)
    except ValidationError as error:
        logger.error("liberar_reservas_vencidas_task: error de validacion: %s", error)
        sentry_sdk.capture_exception(error)
        return {"error": str(error)}
    except Exception as error:
        logger.exception("liberar_reservas_vencidas_task: error inesperado")
        sentry_sdk.capture_exception(error)
        raise

    count = len(results) if results else 0
    logger.info("liberar_reservas_vencidas_task: %s registros procesados", count)
    return {"processed": count}
