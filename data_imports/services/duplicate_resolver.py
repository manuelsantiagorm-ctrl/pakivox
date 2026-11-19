from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from crm.models import Customer, CustomerAddress
from data_imports.models import ImportJob, ImportRowResult

from .customer_writer import (
    _build_existing_maps,
    _create_address_if_complete,
    _create_customer,
    _mapped_customer_fields,
    _register_customer_in_maps,
    _update_customer,
)


ALLOWED_RESOLUTIONS = {
    ImportRowResult.RESOLUTION_LINK_ADDRESS,
    ImportRowResult.RESOLUTION_UPDATE_ADDRESS,
    ImportRowResult.RESOLUTION_SKIP,
    ImportRowResult.RESOLUTION_CREATE_NEW,
}


def duplicate_rows_queryset(job):
    return (
        ImportRowResult.objects
        .for_company(job.company)
        .filter(job=job)
        .filter(
            Q(status=ImportRowResult.STATUS_DUPLICATE)
            | Q(
                status=ImportRowResult.STATUS_SKIPPED,
                matched_model="crm.Customer",
                action_taken__startswith="skipped_existing",
            )
            | Q(
                status=ImportRowResult.STATUS_SKIPPED,
                matched_model="crm.Customer",
                action_taken="duplicate_kept_omitted",
            )
        )
        .order_by("row_number")
    )


def _customer_for_row(job, row):
    if row.matched_object_id:
        try:
            customer_id = int(row.matched_object_id)
        except (TypeError, ValueError):
            customer_id = None

        if customer_id:
            return (
                Customer.objects
                .filter(
                    company=job.company,
                    pk=customer_id,
                )
                .first()
            )

    match_data = row.duplicate_match_data or {}
    customer_id = match_data.get("matched_customer_id")

    if customer_id:
        return (
            Customer.objects
            .filter(
                company=job.company,
                pk=customer_id,
            )
            .first()
        )

    matched_row_number = match_data.get("matched_row_number")

    if matched_row_number:
        source_row = (
            ImportRowResult.objects
            .for_company(job.company)
            .filter(
                job=job,
                row_number=matched_row_number,
            )
            .first()
        )

        if source_row and source_row.matched_object_id:
            try:
                customer_id = int(source_row.matched_object_id)
            except (TypeError, ValueError):
                customer_id = None

            if customer_id:
                return (
                    Customer.objects
                    .filter(
                        company=job.company,
                        pk=customer_id,
                    )
                    .first()
                )

    return None


def build_duplicate_cards(job, rows=None):
    cards = []

    if rows is None:
        rows = duplicate_rows_queryset(job)

    for row in rows:
        customer = _customer_for_row(job, row)
        match_data = row.duplicate_match_data or {}
        source_row = None

        matched_row_number = match_data.get("matched_row_number")
        if matched_row_number:
            source_row = (
                ImportRowResult.objects
                .for_company(job.company)
                .filter(job=job, row_number=matched_row_number)
                .first()
            )

        addresses = []
        if customer:
            addresses = list(
                CustomerAddress.objects
                .filter(
                    company=job.company,
                    customer=customer,
                )
                .order_by("-is_default", "id")
            )

        incoming = row.normalized_data or {}
        incoming_address = incoming.get("address") or {}
        matched_fields = match_data.get("matched_fields") or []
        cards.append(
            {
                "row": row,
                "customer": customer,
                "source_row": source_row,
                "addresses": addresses,
                "incoming_customer": incoming.get("customer") or {},
                "incoming_address": incoming_address,
                "incoming_address_complete": bool(
                    incoming_address
                    and incoming_address.get("is_complete")
                ),
                "matched_fields": matched_fields,
                "confidence": _duplicate_confidence(matched_fields),
                "is_resolved": bool(row.duplicate_resolution),
                "is_applied": bool(row.duplicate_applied_at),
            }
        )

    return cards


def _duplicate_confidence(matched_fields):
    """
    Clasifica la confianza de un duplicado segun los campos que
    coincidieron, para permitir resolucion en bloque en la UI.

    Alta: coincide RFC o email (identificadores unicos por definicion
    de negocio), o coinciden 2 o mas campos a la vez.
    Media: coincide solo un campo de contacto (telefono o whatsapp),
    que puede compartirse legitimamente entre distintas razones
    sociales (ej. mismo numero de conmutador).
    """
    fields = set(matched_fields)
    if not fields:
        return "baja"
    if "rfc" in fields or "email" in fields or len(fields) >= 2:
        return "alta"
    return "media"


def count_duplicates_by_confidence(job):
    """
    Cuenta duplicados pendientes por nivel de confianza sin construir
    las tarjetas completas (sin consultas de cliente/direccion por
    fila), para que sea rapido incluso con miles de duplicados.
    """
    counts = {"alta": 0, "media": 0, "baja": 0}
    pending_rows = (
        duplicate_rows_queryset(job)
        .filter(duplicate_resolution="")
        .only("id", "duplicate_match_data")
    )
    for row in pending_rows:
        matched_fields = (
            (row.duplicate_match_data or {}).get("matched_fields") or []
        )
        counts[_duplicate_confidence(matched_fields)] += 1
    return counts


@transaction.atomic
def bulk_resolve_duplicate_confidence(*, job, user, confidence):
    """
    Resuelve en bloque (accion "mantener existente, omitir") todas las
    filas duplicadas pendientes de un lote que coincidan con el nivel
    de confianza indicado, sin renderizar ni una sola tarjeta. Pensado
    para lotes de miles de duplicados donde un clic por fila no es
    viable.

    Replica el mismo patron de save_duplicate_resolutions: bloquea el
    job, cuenta pendientes tras el update, y avanza job.status a READY
    cuando ya no queda ningun duplicado sin resolver. Sin esto el lote
    quedaba atorado en STATUS_RESOLVING_DUPLICATES para siempre aunque
    la interfaz ya mostrara todo resuelto.
    """
    locked_job = (
        ImportJob.objects
        .select_for_update()
        .get(pk=job.pk, company=job.company)
    )

    pending_rows = (
        duplicate_rows_queryset(locked_job)
        .filter(duplicate_resolution="")
        .only("id", "duplicate_match_data")
    )

    matching_ids = [
        row.id
        for row in pending_rows
        if _duplicate_confidence(
            (row.duplicate_match_data or {}).get("matched_fields") or []
        )
        == confidence
    ]

    if not matching_ids:
        return 0

    updated_count = (
        ImportRowResult.objects
        .filter(pk__in=matching_ids)
        .update(
            duplicate_resolution=ImportRowResult.RESOLUTION_SKIP,
            duplicate_resolution_notes=(
                f"Resuelto en bloque (confianza {confidence})"
            ),
            duplicate_resolved_by=user,
            duplicate_resolved_at=timezone.now(),
            updated_at=timezone.now(),
        )
    )

    unresolved = duplicate_rows_queryset(locked_job).filter(
        duplicate_resolution="",
    ).count()

    if locked_job.status != ImportJob.STATUS_COMPLETED:
        locked_job.status = (
            ImportJob.STATUS_READY
            if unresolved == 0
            else ImportJob.STATUS_RESOLVING_DUPLICATES
        )
        locked_job.error_summary = ""
        locked_job.save(
            update_fields=["status", "error_summary", "updated_at"]
        )

    return updated_count


@transaction.atomic
def save_duplicate_resolutions(*, job, user, post_data):
    locked_job = (
        ImportJob.objects
        .select_for_update()
        .get(pk=job.pk, company=job.company)
    )

    rows = list(
        duplicate_rows_queryset(locked_job)
        .select_for_update()
    )

    if not rows:
        raise ValidationError("El lote no tiene duplicados pendientes.")

    saved = 0

    for row in rows:
        resolution = str(
            post_data.get(f"resolution_{row.pk}", "")
            or ""
        ).strip()

        if not resolution:
            continue

        if resolution not in ALLOWED_RESOLUTIONS:
            raise ValidationError(
                f"La resolución de la fila {row.row_number} no es válida."
            )

        if resolution == ImportRowResult.RESOLUTION_CREATE_NEW:
            confirmation = str(
                post_data.get(f"confirm_create_{row.pk}", "")
                or ""
            ).lower()
            if confirmation not in {"1", "true", "on", "yes"}:
                raise ValidationError(
                    f"Confirma expresamente la creación independiente "
                    f"de la fila {row.row_number}."
                )

        customer = _customer_for_row(locked_job, row)
        if resolution in {
            ImportRowResult.RESOLUTION_LINK_ADDRESS,
            ImportRowResult.RESOLUTION_UPDATE_ADDRESS,
        } and not customer and locked_job.status == ImportJob.STATUS_COMPLETED:
            raise ValidationError(
                f"No se localizó el cliente relacionado para la fila "
                f"{row.row_number}."
            )

        row.duplicate_resolution = resolution
        row.duplicate_resolution_notes = str(
            post_data.get(f"notes_{row.pk}", "")
            or ""
        ).strip()
        row.duplicate_resolved_by = user
        row.duplicate_resolved_at = timezone.now()
        row.save(
            update_fields=[
                "duplicate_resolution",
                "duplicate_resolution_notes",
                "duplicate_resolved_by",
                "duplicate_resolved_at",
                "updated_at",
            ]
        )
        saved += 1

    unresolved = duplicate_rows_queryset(locked_job).filter(
        duplicate_resolution="",
    ).count()

    if locked_job.status != ImportJob.STATUS_COMPLETED:
        locked_job.status = (
            ImportJob.STATUS_READY
            if unresolved == 0
            else ImportJob.STATUS_RESOLVING_DUPLICATES
        )
        locked_job.error_summary = ""
        locked_job.save(
            update_fields=["status", "error_summary", "updated_at"]
        )

    return saved, unresolved


@transaction.atomic
def apply_completed_duplicate_resolutions(*, job, user):
    locked_job = (
        ImportJob.objects
        .select_for_update()
        .get(pk=job.pk, company=job.company)
    )

    if locked_job.status != ImportJob.STATUS_COMPLETED:
        raise ValidationError(
            "Esta operación solo aplica a un lote ya completado."
        )

    rows = list(
        duplicate_rows_queryset(locked_job)
        .select_for_update()
        .exclude(duplicate_resolution="")
        .filter(duplicate_applied_at__isnull=True)
    )

    if not rows:
        raise ValidationError(
            "No hay resoluciones nuevas por aplicar."
        )

    existing_maps = _build_existing_maps(locked_job.company)
    mapped_fields = _mapped_customer_fields(locked_job)

    created = 0
    updated = 0
    activated = 0

    for row in rows:
        resolution = row.duplicate_resolution
        data = row.normalized_data or {}
        customer_data = data.get("customer") or {}
        address_data = data.get("address") or {}
        customer = _customer_for_row(locked_job, row)

        if resolution == ImportRowResult.RESOLUTION_SKIP:
            row.duplicate_resolution_result = "kept_omitted"
            row.duplicate_applied_at = timezone.now()
            row.action_taken = "duplicate_kept_omitted"
            row.save(
                update_fields=[
                    "duplicate_resolution_result",
                    "duplicate_applied_at",
                    "action_taken",
                    "updated_at",
                ]
            )
            continue

        if resolution == ImportRowResult.RESOLUTION_CREATE_NEW:
            customer = _create_customer(
                job=locked_job,
                row=row,
                user=user,
                customer_data=customer_data,
            )
            _register_customer_in_maps(customer, existing_maps)
            created += 1
            action_parts = ["customer_created_duplicate_confirmed"]

        else:
            if not customer or customer.company_id != locked_job.company_id:
                raise ValidationError(
                    f"No se localizó el cliente relacionado para la fila "
                    f"{row.row_number}."
                )

            if resolution == ImportRowResult.RESOLUTION_UPDATE_ADDRESS:
                customer, changed = _update_customer(
                    job=locked_job,
                    row=row,
                    customer=customer,
                    customer_data=customer_data,
                    mapped_fields=mapped_fields,
                )
                if changed:
                    updated += 1
                    action_parts = ["customer_updated_by_resolution"]
                else:
                    action_parts = ["customer_linked_unchanged"]
            elif resolution == ImportRowResult.RESOLUTION_LINK_ADDRESS:
                action_parts = ["customer_linked_existing"]
            else:
                raise ValidationError("Resolución no válida.")

        _, address_action = _create_address_if_complete(
            job=locked_job,
            row=row,
            customer=customer,
            address_data=address_data,
        )
        action_parts.append(address_action)

        row.matched_model = "crm.Customer"
        row.matched_object_id = str(customer.pk)
        row.action_taken = "|".join(action_parts)
        row.duplicate_resolution_result = address_action
        row.duplicate_applied_at = timezone.now()

        if address_action == "address_created" or resolution in {
            ImportRowResult.RESOLUTION_UPDATE_ADDRESS,
            ImportRowResult.RESOLUTION_CREATE_NEW,
        }:
            row.status = ImportRowResult.STATUS_IMPORTED
            activated += 1
        else:
            row.status = ImportRowResult.STATUS_SKIPPED

        row.save(
            update_fields=[
                "status",
                "matched_model",
                "matched_object_id",
                "action_taken",
                "duplicate_resolution_result",
                "duplicate_applied_at",
                "updated_at",
            ]
        )

    locked_job.created_count += created
    locked_job.updated_count += updated
    locked_job.skipped_count = max(
        0,
        locked_job.skipped_count - activated,
    )
    locked_job.save(
        update_fields=[
            "created_count",
            "updated_count",
            "skipped_count",
            "updated_at",
        ]
    )

    return {
        "created": created,
        "updated": updated,
        "activated": activated,
    }
