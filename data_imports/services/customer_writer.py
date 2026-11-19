import sentry_sdk

from datetime import date, datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from crm.models import Customer, CustomerAddress
from custom_fields.services import save_customer_custom_values

from data_imports.models import (
    ImportChangeLog,
    ImportColumnMapping,
    ImportJob,
    ImportRowResult,
)

from .customer_import import (
    normalize_email,
    normalize_phone,
    normalize_rfc,
)


CUSTOMER_FIELDS = (
    "full_name",
    "business_name",
    "rfc",
    "email",
    "phone",
    "whatsapp",
    "customer_type",
    "source",
    "notes",
    "estimated_monthly_purchase",
    "is_active",
)


ADDRESS_FIELDS = (
    "address_type",
    "street",
    "exterior_number",
    "interior_number",
    "neighborhood",
    "city",
    "municipality",
    "state",
    "country",
    "zip_code",
    "is_default",
)


def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    return str(value)


def _snapshot(instance, field_names):
    return {
        field_name: _json_value(
            getattr(
                instance,
                field_name,
                None,
            )
        )
        for field_name in field_names
        if hasattr(
            instance,
            field_name,
        )
    }


def _model_has_field(model_class, field_name):
    return any(
        field.name == field_name
        for field in model_class._meta.fields
    )


def _set_if_field(instance, field_name, value):
    if _model_has_field(
        instance.__class__,
        field_name,
    ):
        setattr(
            instance,
            field_name,
            value,
        )


def _customer_values(customer_data):
    return {
        "full_name": str(
            customer_data.get("full_name")
            or ""
        ).strip(),
        "business_name": (
            str(
                customer_data.get("business_name")
                or ""
            ).strip()
            or None
        ),
        "rfc": (
            str(
                customer_data.get("rfc")
                or ""
            ).strip()
            or None
        ),
        "email": (
            str(
                customer_data.get("email")
                or ""
            ).strip()
            or None
        ),
        "phone": (
            str(
                customer_data.get("phone")
                or ""
            ).strip()
            or None
        ),
        "whatsapp": (
            str(
                customer_data.get("whatsapp")
                or ""
            ).strip()
            or None
        ),
        "customer_type": (
            str(
                customer_data.get("customer_type")
                or "customer"
            ).strip()
        ),
        "source": (
            str(
                customer_data.get("source")
                or "manual"
            ).strip()
        ),
        "notes": (
            str(
                customer_data.get("notes")
                or ""
            ).strip()
            or None
        ),
        "estimated_monthly_purchase": Decimal(
            str(
                customer_data.get(
                    "estimated_monthly_purchase"
                )
                or "0"
            )
        ),
        "is_active": bool(
            customer_data.get(
                "is_active",
                True,
            )
        ),
    }


def _address_is_complete(address_data):
    if not address_data:
        return False

    if address_data.get("is_complete") is False:
        return False

    return all(
        str(
            address_data.get(
                field_name,
                "",
            )
            or ""
        ).strip()
        for field_name in (
            "street",
            "city",
            "state",
            "zip_code",
        )
    )


def _address_values(
    *,
    address_data,
    customer,
):
    values = {
        "address_type": (
            str(
                address_data.get("address_type")
                or "shipping"
            ).strip()
        ),
        "street": str(
            address_data.get("street")
            or ""
        ).strip(),
        "exterior_number": (
            str(
                address_data.get("exterior_number")
                or ""
            ).strip()
            or None
        ),
        "interior_number": (
            str(
                address_data.get("interior_number")
                or ""
            ).strip()
            or None
        ),
        "neighborhood": (
            str(
                address_data.get("neighborhood")
                or ""
            ).strip()
            or None
        ),
        "city": str(
            address_data.get("city")
            or ""
        ).strip(),
        "municipality": str(
            address_data.get("municipality")
            or ""
        ).strip(),
        "state": str(
            address_data.get("state")
            or ""
        ).strip(),
        "country": str(
            address_data.get("country")
            or "México"
        ).strip(),
        "zip_code": str(
            address_data.get("zip_code")
            or ""
        ).strip(),
        "is_default": bool(
            address_data.get(
                "is_default",
                True,
            )
        ),
    }

    optional_values = {
        "label": "Dirección importada",
        "contact_name": customer.full_name,
        "phone": (
            customer.whatsapp
            or customer.phone
            or ""
        ),
        "email": customer.email or "",
        "country_code": "MX",
        "reference": "Importada desde Excel",
    }

    values.update(
        optional_values
    )

    return values


def _build_existing_maps(company):
    maps = {
        "rfc": {},
        "email": {},
        "phone": {},
        "whatsapp": {},
    }
    customers = (
        Customer.objects
        .filter(
            company=company,
        )
    )
    for customer in customers:
        rfc = normalize_rfc(
            customer.rfc
        )

        email = normalize_email(
            customer.email
        )

        phone = normalize_phone(
            customer.phone
        )

        whatsapp = normalize_phone(
            customer.whatsapp
        )

        if rfc:
            maps["rfc"][rfc] = customer

        if email:
            maps["email"][email] = customer

        if phone:
            maps["phone"][phone] = customer

        if whatsapp:
            maps["whatsapp"][whatsapp] = customer

    return maps


def _find_existing_customer(
    customer_data,
    maps,
):
    checks = (
        (
            "rfc",
            normalize_rfc(
                customer_data.get("rfc")
            ),
        ),
        (
            "email",
            normalize_email(
                customer_data.get("email")
            ),
        ),
        (
            "whatsapp",
            normalize_phone(
                customer_data.get("whatsapp")
            ),
        ),
        (
            "phone",
            normalize_phone(
                customer_data.get("phone")
            ),
        ),
    )

    for field_name, value in checks:
        if not value:
            continue

        customer = maps[
            field_name
        ].get(value)

        if customer:
            return customer, field_name

    return None, ""


def _register_customer_in_maps(
    customer,
    maps,
):
    values = {
        "rfc": normalize_rfc(
            customer.rfc
        ),
        "email": normalize_email(
            customer.email
        ),
        "phone": normalize_phone(
            customer.phone
        ),
        "whatsapp": normalize_phone(
            customer.whatsapp
        ),
    }

    for field_name, value in values.items():
        if value:
            maps[
                field_name
            ][value] = customer


def _mapped_customer_fields(job):
    target_fields = set(
        ImportColumnMapping.objects
        .filter(
            company=job.company,
            job=job,
            target_field__startswith="customer.",
        )
        .values_list(
            "target_field",
            flat=True,
        )
    )

    return {
        target_field.split(
            ".",
            1,
        )[1]
        for target_field in target_fields
        if "." in target_field
    }


def _create_change_log(
    *,
    job,
    row,
    action,
    instance,
    before_data=None,
    after_data=None,
):
    ImportChangeLog.objects.create(
        company=job.company,
        job=job,
        row_result=row,
        action=action,
        model_label=(
            f"{instance._meta.app_label}."
            f"{instance.__class__.__name__}"
        ),
        object_pk=str(
            instance.pk
        ),
        before_data=(
            before_data
            or {}
        ),
        after_data=(
            after_data
            or {}
        ),
    )


def _create_customer(
    *,
    job,
    row,
    user,
    customer_data,
):
    values = _customer_values(
        customer_data
    )

    customer = Customer(
        company=job.company,
        assigned_to=user,
        **values,
    )

    customer.full_clean()
    customer.save()

    _create_change_log(
        job=job,
        row=row,
        action=ImportChangeLog.ACTION_CREATED,
        instance=customer,
        after_data=_snapshot(
            customer,
            CUSTOMER_FIELDS,
        ),
    )

    return customer


def _update_customer(
    *,
    job,
    row,
    customer,
    customer_data,
    mapped_fields,
):
    before_data = _snapshot(
        customer,
        CUSTOMER_FIELDS,
    )

    values = _customer_values(
        customer_data
    )

    changed_fields = []

    for field_name in mapped_fields:
        if field_name not in values:
            continue

        value = values[
            field_name
        ]

        if field_name != "is_active" and value in {
            None,
            "",
        }:
            continue

        if getattr(
            customer,
            field_name,
            None,
        ) != value:
            setattr(
                customer,
                field_name,
                value,
            )
            changed_fields.append(
                field_name
            )

    if not changed_fields:
        return customer, False

    customer.full_clean()
    customer.save(
        update_fields=(
            changed_fields
            + ["updated_at"]
        )
    )

    _create_change_log(
        job=job,
        row=row,
        action=ImportChangeLog.ACTION_UPDATED,
        instance=customer,
        before_data=before_data,
        after_data=_snapshot(
            customer,
            CUSTOMER_FIELDS,
        ),
    )

    return customer, True


def _create_address_if_complete(
    *,
    job,
    row,
    customer,
    address_data,
):
    if not _address_is_complete(
        address_data
    ):
        return None, "address_omitted"

    values = _address_values(
        address_data=address_data,
        customer=customer,
    )

    existing_address = (
        CustomerAddress.objects
        .filter(
            company=job.company,
            customer=customer,
            address_type=values["address_type"],
            street=values["street"],
            exterior_number=values["exterior_number"],
            interior_number=values["interior_number"],
            neighborhood=values["neighborhood"],
            city=values["city"],
            municipality=values["municipality"],
            state=values["state"],
            country=values["country"],
            zip_code=values["zip_code"],
        )
        .first()
    )

    if existing_address:
        return existing_address, "address_existing"

    if values.get("is_default"):
        already_has_default = (
            CustomerAddress.objects
            .filter(
                company=job.company,
                customer=customer,
                is_default=True,
            )
            .exists()
        )

        if already_has_default:
            values[
                "is_default"
            ] = False

    address = CustomerAddress(
        company=job.company,
        customer=customer,
    )

    for field_name, value in values.items():
        _set_if_field(
            address,
            field_name,
            value,
        )

    address.full_clean()
    address.save()

    snapshot_fields = [
        field.name
        for field in CustomerAddress._meta.fields
        if field.name not in {
            "id",
            "company",
            "customer",
            "created_at",
            "updated_at",
        }
    ]

    _create_change_log(
        job=job,
        row=row,
        action=ImportChangeLog.ACTION_CREATED,
        instance=address,
        after_data=_snapshot(
            address,
            snapshot_fields,
        ),
    )

    return address, "address_created"


def _resolution_for_row(row, job):
    if row.duplicate_resolution:
        return row.duplicate_resolution

    if job.duplicate_policy == ImportJob.DUPLICATE_SKIP:
        return ImportRowResult.RESOLUTION_SKIP

    if job.duplicate_policy == ImportJob.DUPLICATE_UPDATE:
        return ImportRowResult.RESOLUTION_UPDATE_ADDRESS

    if job.duplicate_policy == ImportJob.DUPLICATE_CREATE:
        return ImportRowResult.RESOLUTION_CREATE_NEW

    return ""


def import_customer_job(
    *,
    job,
    user,
):
    with transaction.atomic():
        locked_job = (
            ImportJob.objects
            .select_for_update()
            .get(
                pk=job.pk,
                company=job.company,
            )
        )
        if locked_job.status != ImportJob.STATUS_READY:
            raise ValidationError(
                "El lote debe estar en estado \u201cListo para importar\u201d."
            )

        row_count = (
            ImportRowResult.objects
            .filter(
                company=locked_job.company,
                job=locked_job,
            )
            .count()
        )
        if not row_count:
            raise ValidationError(
                "El lote no tiene filas validadas."
            )

        unresolved_duplicates = (
            ImportRowResult.objects
            .filter(
                company=locked_job.company,
                job=locked_job,
                status=ImportRowResult.STATUS_DUPLICATE,
            )
        )
        for row in unresolved_duplicates:
            if not _resolution_for_row(row, locked_job):
                raise ValidationError(
                    "Primero debes resolver todos los duplicados del lote."
                )

        locked_job.status = ImportJob.STATUS_PROCESSING
        locked_job.started_at = timezone.now()
        locked_job.finished_at = None
        locked_job.error_summary = ""
        locked_job.created_count = 0
        locked_job.updated_count = 0
        locked_job.skipped_count = 0
        locked_job.failed_count = 0
        locked_job.processed_count = 0
        locked_job.save(
            update_fields=[
                "status",
                "started_at",
                "finished_at",
                "error_summary",
                "created_count",
                "updated_count",
                "skipped_count",
                "failed_count",
                "processed_count",
                "updated_at",
            ]
        )

    # A partir de aqui ya no hay una transaccion exterior abierta: cada
    # fila se procesa y confirma de forma independiente (su propio
    # transaction.atomic), para que el progreso sea visible en tiempo
    # real desde otras conexiones (polling del frontend) y para no
    # mantener bloqueados el job ni las filas durante todo el proceso
    # cuando el lote es grande.

    existing_maps = _build_existing_maps(locked_job.company)
    mapped_fields = _mapped_customer_fields(locked_job)

    row_ids = list(
        ImportRowResult.objects
        .filter(
            company=locked_job.company,
            job=locked_job,
        )
        .order_by("row_number")
        .values_list("pk", flat=True)
    )

    created_count = 0
    updated_count = 0
    skipped_count = 0
    failed_count = 0
    total_rows_to_process = len(row_ids)

    for row_index, row_id in enumerate(row_ids, start=1):
        with transaction.atomic():
            row = (
                ImportRowResult.objects
                .select_for_update()
                .get(pk=row_id)
            )

            if row_index % 20 == 0 or row_index == total_rows_to_process:
                ImportJob.objects.filter(
                    company=locked_job.company,
                    pk=locked_job.pk,
                ).update(processed_count=row_index)

            if row.status in {
                ImportRowResult.STATUS_IMPORTED,
                ImportRowResult.STATUS_SKIPPED,
            }:
                continue

            if row.status == ImportRowResult.STATUS_ERROR:
                row.action_taken = "not_imported_error"
                row.save(update_fields=["action_taken", "updated_at"])
                failed_count += 1
                continue

            try:
                normalized_data = row.normalized_data or {}
                customer_data = normalized_data.get("customer") or {}
                address_data = normalized_data.get("address") or {}
                custom_values = normalized_data.get("custom_fields") or {}

                existing_customer, matched_by = _find_existing_customer(
                    customer_data,
                    existing_maps,
                )

                is_duplicate = (
                    row.status == ImportRowResult.STATUS_DUPLICATE
                    or existing_customer is not None
                )

                if is_duplicate:
                    resolution = _resolution_for_row(row, locked_job)

                    if not resolution:
                        raise ValidationError(
                            "El duplicado de la fila "
                            f"{row.row_number} no tiene resoluci\u00f3n."
                        )

                    if resolution == ImportRowResult.RESOLUTION_SKIP:
                        if existing_customer:
                            row.matched_model = "crm.Customer"
                            row.matched_object_id = str(existing_customer.pk)
                        row.status = ImportRowResult.STATUS_SKIPPED
                        row.action_taken = "duplicate_kept_omitted"
                        row.duplicate_resolution_result = "kept_omitted"
                        row.duplicate_applied_at = timezone.now()
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
                        skipped_count += 1
                        continue

                    if resolution == ImportRowResult.RESOLUTION_CREATE_NEW:
                        customer = _create_customer(
                            job=locked_job,
                            row=row,
                            user=user,
                            customer_data=customer_data,
                        )
                        created_count += 1
                        action_parts = ["customer_created_duplicate_confirmed"]
                    else:
                        if not existing_customer:
                            raise ValidationError(
                                "No fue posible localizar el cliente relacionado "
                                f"para la fila {row.row_number}."
                            )
                        if existing_customer.company_id != locked_job.company_id:
                            raise ValidationError(
                                "Se detect\u00f3 un cliente de otra empresa."
                            )
                        customer = existing_customer
                        row.matched_model = "crm.Customer"
                        row.matched_object_id = str(customer.pk)

                        if resolution == ImportRowResult.RESOLUTION_UPDATE_ADDRESS:
                            customer, changed = _update_customer(
                                job=locked_job,
                                row=row,
                                customer=customer,
                                customer_data=customer_data,
                                mapped_fields=mapped_fields,
                            )
                            if changed:
                                updated_count += 1
                                action_parts = ["customer_updated_by_resolution"]
                            else:
                                action_parts = ["customer_linked_unchanged"]
                        elif resolution == ImportRowResult.RESOLUTION_LINK_ADDRESS:
                            action_parts = ["customer_linked_existing"]
                        else:
                            raise ValidationError(
                                "La resoluci\u00f3n del duplicado no es v\u00e1lida."
                            )
                else:
                    customer = _create_customer(
                        job=locked_job,
                        row=row,
                        user=user,
                        customer_data=customer_data,
                    )
                    created_count += 1
                    action_parts = ["customer_created"]

                _register_customer_in_maps(customer, existing_maps)

                _, address_action = _create_address_if_complete(
                    job=locked_job,
                    row=row,
                    customer=customer,
                    address_data=address_data,
                )
                action_parts.append(address_action)

                save_customer_custom_values(
                    company=locked_job.company,
                    customer=customer,
                    values=custom_values,
                )

                row.status = ImportRowResult.STATUS_IMPORTED
                row.matched_model = "crm.Customer"
                row.matched_object_id = str(customer.pk)
                row.action_taken = "|".join(action_parts)
                if is_duplicate:
                    row.duplicate_resolution_result = address_action
                    row.duplicate_applied_at = timezone.now()
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
            except Exception as row_error:
                sentry_sdk.capture_exception(row_error)

                if hasattr(row_error, "message_dict"):
                    error_message = "; ".join(
                        f"{field}: {', '.join(msgs)}"
                        for field, msgs in row_error.message_dict.items()
                    )
                elif hasattr(row_error, "messages"):
                    error_message = "; ".join(row_error.messages)
                else:
                    error_message = str(row_error)

                row.status = ImportRowResult.STATUS_ERROR
                row.action_taken = "not_imported_error"
                row.errors = [error_message]
                row.save(
                    update_fields=[
                        "status",
                        "action_taken",
                        "errors",
                        "updated_at",
                    ]
                )
                failed_count += 1
                continue

    with transaction.atomic():
        locked_job = (
            ImportJob.objects
            .select_for_update()
            .get(pk=job.pk, company=job.company)
        )
        locked_job.created_count = created_count
        locked_job.updated_count = updated_count
        locked_job.skipped_count = skipped_count
        locked_job.failed_count = failed_count
        locked_job.processed_count = total_rows_to_process

        if failed_count and not (created_count or updated_count or skipped_count):
            locked_job.status = ImportJob.STATUS_FAILED
            locked_job.error_summary = (
                f"Ninguna fila pudo importarse. {failed_count} filas con error."
            )
        else:
            locked_job.status = ImportJob.STATUS_COMPLETED
            locked_job.error_summary = (
                f"{failed_count} filas con error." if failed_count else ""
            )

        locked_job.finished_at = timezone.now()
        locked_job.save(
            update_fields=[
                "created_count",
                "updated_count",
                "skipped_count",
                "failed_count",
                "processed_count",
                "status",
                "finished_at",
                "error_summary",
                "updated_at",
            ]
        )

    return {
        "created_count": created_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
    }
