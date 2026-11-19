"""
Tareas de Celery para procesar importaciones masivas en segundo plano
(BUG-078). El procesamiento pesado (validar miles de filas, escribir
a la base de datos) ya no bloquea el request HTTP del usuario.
"""

import logging

import sentry_sdk
from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from .models import ImportJob

logger = logging.getLogger(__name__)

User = get_user_model()


def _resolve_job_and_user(job_id, user_id):
    """
    Reconstruye los objetos reales a partir de sus IDs. Las tareas de
    Celery nunca reciben objetos de Django directamente porque se
    serializan a JSON para pasar por Redis.
    """
    job = ImportJob.objects.select_related("company").get(pk=job_id)
    user = User.objects.get(pk=user_id)
    return job, user


def _handle_import_failure(job, error):
    """
    Marca el job como fallido de forma consistente, igual que ya
    hacían las vistas síncronas antes de esta migración.

    No sobrescribe un job que ya fue marcado como completado por otra
    ejecución concurrente de la misma tarea (protección contra
    doble-submit / condiciones de carrera).
    """
    logger.exception(
        "Error al procesar importación en segundo plano. " "company_id=%s job_id=%s",
        job.company_id,
        job.pk,
    )
    if isinstance(error, BaseException):
        sentry_sdk.capture_exception(error)
    else:
        sentry_sdk.capture_message(str(error), level="error")
    updated = (
        ImportJob.objects.filter(
            company=job.company,
            pk=job.pk,
        )
        .exclude(
            status=ImportJob.STATUS_COMPLETED,
        )
        .update(
            status=ImportJob.STATUS_FAILED,
            error_summary=(
                str(error)
                if settings.DEBUG
                else "No fue posible completar la importación. Contacta a soporte."
            ),
        )
    )
    if not updated:
        logger.info(
            "Se omitió marcar como fallido el job %s: ya estaba completado "
            "por otra ejecución.",
            job.pk,
        )


@shared_task(bind=True, max_retries=0)
def process_customer_import_task(self, job_id, user_id):
    from .services.customer_writer import import_customer_job

    job, user = _resolve_job_and_user(job_id, user_id)

    try:
        result = import_customer_job(job=job, user=user)
    except ValidationError as error:
        _handle_import_failure(job, error)
        return {"ok": False, "error": str(error)}
    except Exception as error:
        _handle_import_failure(job, error)
        return {"ok": False, "error": str(error)}

    ImportJob.objects.filter(company=job.company, pk=job.pk).update(
        status=ImportJob.STATUS_COMPLETED,
        error_summary="",
    )

    return {"ok": True, **result}


@shared_task(bind=True, max_retries=0)
def process_supplier_import_task(self, job_id, user_id):
    from .services.supplier_writer import import_supplier_job

    job, user = _resolve_job_and_user(job_id, user_id)

    try:
        result = import_supplier_job(job=job, user=user)
    except ValidationError as error:
        _handle_import_failure(job, " ".join(error.messages))
        return {"ok": False, "error": " ".join(error.messages)}
    except Exception as error:
        sentry_sdk.capture_exception(error)
        _handle_import_failure(job, error)
        return {"ok": False, "error": str(error)}

    return {"ok": True, **result}


@shared_task(bind=True, max_retries=0)
def process_product_import_task(self, job_id, user_id):
    from .services.product_writer import import_product_job, validate_product_rows
    from .services.product_import import get_product_mapping
    from .services.tabular_reader import ImportFileError
    from .views import _rows_with_saved_corrections, load_job_file

    job, user = _resolve_job_and_user(job_id, user_id)
    mapping = get_product_mapping(job)

    try:
        file_data = load_job_file(job)
        corrected_rows = _rows_with_saved_corrections(job, file_data)

        options = job.options if isinstance(job.options, dict) else {}
        skipped_numbers = {
            int(value)
            for value in options.get("skipped_row_numbers", [])
            if str(value).isdigit()
        }

        rows_to_import = []
        for row in corrected_rows:
            row_number = int(row.get("row_number", 0) or 0)
            if row_number in skipped_numbers:
                continue
            rows_to_import.append(row)

        if not rows_to_import:
            raise ValidationError(
                "No quedan filas disponibles para importar. "
                "Revisa las filas omitidas del lote."
            )

        final_validation = validate_product_rows(
            job=job,
            headers=file_data["headers"],
            rows=rows_to_import,
            mapping=mapping,
        )
        if final_validation["error_rows"] > 0:
            raise ValidationError(
                "El lote volvió a presentar errores durante la "
                "validación final. Revisa las observaciones antes "
                "de importar."
            )

        result = import_product_job(
            job=job,
            headers=file_data["headers"],
            rows=rows_to_import,
            mapping=mapping,
            user=user,
        )

    except (ImportFileError, ValidationError) as error:
        logger.warning(
            "Importación de productos rechazada en segundo plano. "
            "job=%s company=%s error=%s",
            job.pk,
            job.company_id,
            error,
        )
        _handle_import_failure(job, error)
        return {"ok": False, "error": str(error)}
    except Exception as error:
        sentry_sdk.capture_exception(error)
        _handle_import_failure(job, error)
        return {"ok": False, "error": str(error)}

    ImportJob.objects.filter(company=job.company, pk=job.pk).update(
        status=ImportJob.STATUS_COMPLETED,
        error_summary="",
    )

    return {"ok": True, **result}


@shared_task(bind=True, max_retries=0)
def process_inventory_import_task(self, job_id, user_id, warehouse_id):
    from inventory.models import Warehouse

    from .services.inventory_import import (
        get_inventory_mapping,
        import_inventory_job,
        validate_inventory_rows,
    )
    from .services.tabular_reader import ImportFileError
    from .views import _rows_with_saved_corrections, load_job_file

    job, user = _resolve_job_and_user(job_id, user_id)
    warehouse = Warehouse.objects.get(pk=warehouse_id, company=job.company)
    mapping = get_inventory_mapping(job)

    try:
        file_data = load_job_file(job)
        corrected_rows = _rows_with_saved_corrections(job, file_data)

        options = job.options if isinstance(job.options, dict) else {}
        skipped_numbers = {
            int(value)
            for value in options.get("skipped_row_numbers", [])
            if str(value).isdigit()
        }

        rows_to_import = []
        for row in corrected_rows:
            row_number = int(row.get("row_number", 0) or 0)
            if row_number in skipped_numbers:
                continue
            rows_to_import.append(row)

        if not rows_to_import:
            raise ValidationError("No quedan filas disponibles para importar.")

        final_validation = validate_inventory_rows(
            job=job,
            warehouse=warehouse,
            headers=file_data["headers"],
            rows=rows_to_import,
            mapping=mapping,
        )
        if final_validation["error_rows"] > 0:
            raise ValidationError(
                "El lote volvió a presentar errores durante la validación final."
            )

        result = import_inventory_job(
            job=job,
            warehouse=warehouse,
            headers=file_data["headers"],
            rows=rows_to_import,
            mapping=mapping,
            user=user,
        )

    except (ImportFileError, ValidationError) as error:
        logger.warning(
            "Importación de inventario rechazada en segundo plano. "
            "job=%s company=%s error=%s",
            job.pk,
            job.company_id,
            error,
        )
        _handle_import_failure(job, error)
        return {"ok": False, "error": str(error)}
    except Exception as error:
        _handle_import_failure(job, error)
        return {"ok": False, "error": str(error)}

    ImportJob.objects.filter(company=job.company, pk=job.pk).update(
        status=ImportJob.STATUS_COMPLETED,
        error_summary="",
    )

    return {"ok": True, **result}
