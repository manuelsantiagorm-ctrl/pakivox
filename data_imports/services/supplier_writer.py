from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from purchases.models import Supplier

from data_imports.models import ImportChangeLog, ImportJob, ImportRowResult


SUPPLIER_FIELDS = (
    "name", "legal_name", "rfc", "contact_name", "email", "phone",
    "whatsapp", "address", "city", "state", "country", "payment_terms",
    "credit_days", "notes", "is_active",
)


def _snapshot(instance):
    return {field: getattr(instance, field) for field in SUPPLIER_FIELDS}


@transaction.atomic
def import_supplier_job(*, job, user):
    job = ImportJob.objects.select_for_update().get(
        pk=job.pk, company=job.company, entity_type=ImportJob.ENTITY_SUPPLIERS
    )
    if job.status not in {ImportJob.STATUS_READY, ImportJob.STATUS_FAILED}:
        raise ValidationError("Este lote no está listo para importar.")

    job.status = ImportJob.STATUS_PROCESSING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at", "updated_at"])

    created = updated = skipped = 0
    rows = ImportRowResult.objects.select_for_update().filter(
        company=job.company, job=job
    ).order_by("row_number")

    for row in rows:
        if row.status in {
            ImportRowResult.STATUS_ERROR,
            ImportRowResult.STATUS_SKIPPED,
        }:
            skipped += 1
            continue

        resolution = row.duplicate_resolution
        if row.status == ImportRowResult.STATUS_DUPLICATE:
            if job.duplicate_policy == ImportJob.DUPLICATE_SKIP or resolution == "skip":
                row.status = ImportRowResult.STATUS_SKIPPED
                row.action_taken = "Proveedor existente omitido"
                row.save(update_fields=["status", "action_taken", "updated_at"])
                skipped += 1
                continue
            update_existing = (
                job.duplicate_policy == ImportJob.DUPLICATE_UPDATE
                or resolution == "update_address"
            )
            create_new = (
                job.duplicate_policy == ImportJob.DUPLICATE_CREATE
                or resolution == "create_new"
            )
            if not update_existing and not create_new:
                raise ValidationError("Faltan duplicados de proveedores por resolver.")
        else:
            update_existing = False
            create_new = True

        values = {
            key: value
            for key, value in row.normalized_data.get("supplier", {}).items()
            if key in SUPPLIER_FIELDS
        }
        supplier = None
        before = {}
        action = ImportChangeLog.ACTION_CREATED

        if update_existing:
            supplier = Supplier.objects.select_for_update().filter(
                company=job.company, pk=row.matched_object_id
            ).first()
            if supplier is None:
                raise ValidationError("El proveedor duplicado ya no existe.")
            before = _snapshot(supplier)
            for key, value in values.items():
                setattr(supplier, key, value)
            supplier.full_clean()
            supplier.save()
            updated += 1
            action = ImportChangeLog.ACTION_UPDATED
        else:
            if Supplier.objects.filter(
                company=job.company, name__iexact=values.get("name", "")
            ).exists():
                if create_new and row.status == ImportRowResult.STATUS_DUPLICATE:
                    base_name = values["name"][:165]
                    suffix = 2
                    candidate = f"{base_name} ({suffix})"
                    while Supplier.objects.filter(
                        company=job.company, name__iexact=candidate
                    ).exists():
                        suffix += 1
                        candidate = f"{base_name} ({suffix})"
                    values["name"] = candidate
                else:
                    row.status = ImportRowResult.STATUS_SKIPPED
                    row.action_taken = "Nombre duplicado omitido"
                    row.save(update_fields=["status", "action_taken", "updated_at"])
                    skipped += 1
                    continue
            supplier = Supplier(company=job.company, created_by=user, **values)
            supplier.full_clean()
            supplier.save()
            created += 1

        from custom_fields.services import save_supplier_custom_values
        save_supplier_custom_values(
            company=job.company,
            supplier=supplier,
            values=row.normalized_data.get("custom", {}),
        )
        ImportChangeLog.objects.create(
            company=job.company, job=job, row_result=row, action=action,
            model_label="purchases.Supplier", object_pk=str(supplier.pk),
            before_data=before, after_data=_snapshot(supplier),
        )
        row.status = ImportRowResult.STATUS_IMPORTED
        row.action_taken = "Proveedor actualizado" if update_existing else "Proveedor creado"
        row.matched_model = "purchases.Supplier"
        row.matched_object_id = str(supplier.pk)
        row.save(update_fields=[
            "status", "action_taken", "matched_model", "matched_object_id", "updated_at"
        ])

    job.status = ImportJob.STATUS_COMPLETED
    job.created_count = created
    job.updated_count = updated
    job.skipped_count = skipped
    job.finished_at = timezone.now()
    job.error_summary = ""
    job.save(update_fields=[
        "status", "created_count", "updated_count", "skipped_count",
        "finished_at", "error_summary", "updated_at",
    ])
    return {"created_count": created, "updated_count": updated, "skipped_count": skipped}
