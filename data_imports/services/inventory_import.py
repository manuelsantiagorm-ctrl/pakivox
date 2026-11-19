import re
import unicodedata
from django.db import transaction

from inventory.stock_services import register_manual_stock_entry

from django.core.exceptions import ValidationError

from data_imports.models import (
    ImportColumnMapping,
    ImportJob,
    ImportRowResult,
)
from inventory.models import ProductVariant, Stock

# ============================================================================
# CAMPOS DISPONIBLES
# ============================================================================

INVENTORY_TARGET_CHOICES = [
    ("", "Ignorar columna"),
    ("inventory.sku", "Inventario · SKU"),
    ("inventory.quantity", "Inventario · Cantidad"),
    ("inventory.reference", "Inventario · Referencia"),
    ("inventory.notes", "Inventario · Notas"),
]


# ============================================================================
# ALIAS DE ENCABEZADOS
# ============================================================================

HEADER_ALIASES = {
    "inventory.sku": {
        "sku",
        "codigo sku",
        "código sku",
        "clave sku",
        "codigo producto",
        "código producto",
        "product sku",
    },
    "inventory.quantity": {
        "cantidad",
        "stock",
        "stock inicial",
        "inventario",
        "inventario inicial",
        "existencia",
        "existencias",
        "cantidad inicial",
        "quantity",
        "initial stock",
        "stock inicial unidades",
    },
    "inventory.reference": {
        "referencia",
        "folio",
        "documento",
        "origen",
        "reference",
    },
    "inventory.notes": {
        "notas",
        "nota",
        "observaciones",
        "comentarios",
        "comments",
        "notes",
    },
}


# ============================================================================
# HELPERS
# ============================================================================


def _text(value):
    return str(value or "").strip()


def _key(value):
    value = _text(value)

    value = unicodedata.normalize(
        "NFKD",
        value,
    )

    value = "".join(char for char in value if not unicodedata.combining(char))

    return re.sub(
        r"[^a-z0-9]+",
        " ",
        value.lower(),
    ).strip()


def _row_values(row):
    if isinstance(row, dict):
        return row.get(
            "values",
            [],
        )

    return row


def _row_number(
    row,
    fallback,
):
    if isinstance(row, dict):
        try:
            return int(
                row.get(
                    "row_number",
                    fallback,
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            return fallback

    return fallback


# ============================================================================
# SUGERIR MAPPING
# ============================================================================


def suggest_inventory_mapping(
    headers,
):
    result = {}
    used = set()

    for index, header in enumerate(headers):
        header_key = _key(header)

        selected = ""

        for target, aliases in HEADER_ALIASES.items():
            if target in used:
                continue

            normalized_aliases = {_key(alias) for alias in aliases}

            if header_key in normalized_aliases:
                selected = target
                break

        result[str(index)] = selected

        if selected:
            used.add(selected)

    return result


# ============================================================================
# OBTENER MAPPING
# ============================================================================


def get_inventory_mapping(job):
    mapping = (
        job.proposed_mapping
        if isinstance(
            job.proposed_mapping,
            dict,
        )
        else {}
    )

    return {str(key): str(value or "") for key, value in mapping.items()}


# ============================================================================
# GUARDAR MAPPING
# ============================================================================


def persist_inventory_mapping(
    *,
    job,
    headers,
    mapping,
):
    clean_mapping = {
        str(index): str(
            mapping.get(
                str(index),
                "",
            )
            or ""
        )
        for index, _header in enumerate(headers)
    }

    job.proposed_mapping = clean_mapping

    job.save(
        update_fields=[
            "proposed_mapping",
            "updated_at",
        ]
    )

    ImportColumnMapping.objects.filter(
        company=job.company,
        job=job,
    ).delete()

    mapping_rows = []

    for index, header in enumerate(headers):
        mapping_rows.append(
            ImportColumnMapping(
                company=job.company,
                job=job,
                source_column=str(header),
                source_index=index,
                target_field=clean_mapping.get(
                    str(index),
                    "",
                ),
                confirmed_by_user=True,
                is_required=(
                    clean_mapping.get(
                        str(index),
                        "",
                    )
                    in {
                        "inventory.sku",
                        "inventory.quantity",
                    }
                ),
            )
        )

    ImportColumnMapping.objects.bulk_create(mapping_rows)

    return clean_mapping


# ============================================================================
# NORMALIZAR FILA
# ============================================================================


def normalize_inventory_row(
    *,
    row,
    mapping,
    row_number,
):
    values = _row_values(row)

    mapped_data = {}

    for source_index, target_field in mapping.items():
        if not target_field:
            continue

        try:
            index = int(source_index)
        except (
            TypeError,
            ValueError,
        ):
            continue

        mapped_data[target_field] = values[index] if index < len(values) else ""

    return {
        "row_number": row_number,
        "sku": _text(
            mapped_data.get(
                "inventory.sku",
                "",
            )
        ),
        "quantity_raw": mapped_data.get(
            "inventory.quantity",
            "",
        ),
        "reference": _text(
            mapped_data.get(
                "inventory.reference",
                "",
            )
        ),
        "notes": _text(
            mapped_data.get(
                "inventory.notes",
                "",
            )
        ),
        "mapped_data": mapped_data,
    }


# ============================================================================
# VALIDAR FILAS
# ============================================================================


def validate_inventory_rows(
    *,
    job,
    warehouse,
    headers,
    rows,
    mapping,
):
    company = job.company

    # ==========================================================
    # SEGURIDAD DE EMPRESA / BODEGA
    # ==========================================================

    if not warehouse:
        raise ValidationError("Selecciona una bodega específica.")

    if warehouse.company_id != company.id:
        raise ValidationError("La bodega seleccionada no pertenece a esta empresa.")

    if not warehouse.is_active:
        raise ValidationError("No puedes importar inventario a una bodega inactiva.")

    if job.duplicate_policy not in {
        ImportJob.DUPLICATE_STOP,
        ImportJob.DUPLICATE_UPDATE,
    }:
        raise ValidationError(
            "La política de inventario configurada para este lote no es válida."
        )

    # ==========================================================
    # REGENERAR ANÁLISIS DEL LOTE
    # ==========================================================

    ImportRowResult.objects.filter(
        company=company,
        job=job,
    ).delete()

    counters = {
        "total_rows": 0,
        "valid_rows": 0,
        "warning_rows": 0,
        "error_rows": 0,
        "duplicate_rows": 0,
    }

    row_results = []

    # Evita que un mismo SKU aparezca dos veces
    # dentro del mismo Excel.
    seen_skus = set()

    # ==========================================================
    # RECORRER FILAS
    # ==========================================================

    for fallback_number, row in enumerate(
        rows,
        start=2,
    ):
        row_number = _row_number(
            row,
            fallback_number,
        )

        source_values = _row_values(row)

        data = normalize_inventory_row(
            row=row,
            mapping=mapping,
            row_number=row_number,
        )

        # ======================================================
        # IGNORAR FILAS COMPLETAMENTE VACÍAS
        # ======================================================

        if not any(_text(value) for value in source_values):
            continue

        counters["total_rows"] += 1

        errors = []
        warnings = []

        error_details = []
        warning_details = []

        # ======================================================
        # SKU
        # ======================================================

        sku = data["sku"]

        variant = None

        if not sku:
            message = "El SKU es obligatorio."

            errors.append(message)

            error_details.append(
                {
                    "target_field": "inventory.sku",
                    "message": message,
                }
            )

        else:
            # IMPORTANTE:
            # Nunca buscamos SKU globalmente.
            # Debe pertenecer a la empresa actual.
            variant = (
                ProductVariant.objects.select_related(
                    "product",
                    "company",
                )
                .filter(
                    company=company,
                    product__company=company,
                    sku=sku,
                )
                .first()
            )

            if not variant:
                message = f"El SKU '{sku}' no existe " "en el catálogo de esta empresa."

                errors.append(message)

                error_details.append(
                    {
                        "target_field": "inventory.sku",
                        "message": message,
                    }
                )

            elif not variant.is_active:
                message = f"El SKU '{sku}' está inactivo."

                errors.append(message)

                error_details.append(
                    {
                        "target_field": "inventory.sku",
                        "message": message,
                    }
                )

            elif variant.is_bundle:
                message = (
                    f"El SKU '{sku}' corresponde a un combo virtual "
                    "y no puede recibir stock directo."
                )

                errors.append(message)

                error_details.append(
                    {
                        "target_field": "inventory.sku",
                        "message": message,
                    }
                )

        # ======================================================
        # SKU REPETIDO DENTRO DEL MISMO EXCEL
        # ======================================================

        if sku:
            normalized_sku = sku.casefold()

            if normalized_sku in seen_skus:
                message = (
                    f"El SKU '{sku}' aparece más de una vez " "en el mismo archivo."
                )

                errors.append(message)

                error_details.append(
                    {
                        "target_field": "inventory.sku",
                        "message": message,
                    }
                )

            seen_skus.add(normalized_sku)

        # ======================================================
        # CANTIDAD
        # ======================================================

        quantity = None

        quantity_raw = data["quantity_raw"]

        if _text(quantity_raw) == "":
            message = "La cantidad es obligatoria."

            errors.append(message)

            error_details.append(
                {
                    "target_field": "inventory.quantity",
                    "message": message,
                }
            )

        else:
            try:
                # Excel puede devolver 50 como int,
                # pero también 50.0 como float.
                if isinstance(
                    quantity_raw,
                    bool,
                ):
                    raise ValueError

                numeric_value = float(str(quantity_raw).strip())

                if not numeric_value.is_integer():
                    raise ValueError

                quantity = int(numeric_value)

            except (
                TypeError,
                ValueError,
                OverflowError,
            ):
                message = "La cantidad debe ser un número entero."

                errors.append(message)

                error_details.append(
                    {
                        "target_field": "inventory.quantity",
                        "message": message,
                    }
                )

            else:
                if quantity <= 0:
                    message = "La cantidad debe ser mayor que cero."

                    errors.append(message)

                    error_details.append(
                        {
                            "target_field": "inventory.quantity",
                            "message": message,
                        }
                    )

        # ======================================================
        # STOCK ACTUAL EN ESTA BODEGA
        #
        # IMPORTANTE:
        #
        # Si el SKU existe en OTRA bodega:
        #     NO PASA NADA.
        #
        # Solo consultamos:
        #
        # company + warehouse actual + variant
        # ======================================================

        existing_stock = None

        if variant:
            existing_stock = Stock.objects.filter(
                company=company,
                warehouse=warehouse,
                variant=variant,
            ).first()

        existing_physical_stock = existing_stock.physical_stock if existing_stock else 0

        # ======================================================
        # YA TIENE STOCK EN ESTA MISMA BODEGA
        # ======================================================

        if existing_stock and existing_physical_stock > 0:
            # --------------------------------------------------
            # POLÍTICA: DETENER
            # --------------------------------------------------

            if job.duplicate_policy == ImportJob.DUPLICATE_STOP:
                message = (
                    f"El SKU '{sku}' ya tiene "
                    f"{existing_physical_stock} unidades físicas "
                    f"en la bodega {warehouse.name}. "
                    "La política actual impide agregar inventario "
                    "sin revisión."
                )

                errors.append(message)

                error_details.append(
                    {
                        "target_field": "inventory.quantity",
                        "message": message,
                    }
                )

            # --------------------------------------------------
            # POLÍTICA: REGISTRAR NUEVA ENTRADA
            # --------------------------------------------------

            elif job.duplicate_policy == ImportJob.DUPLICATE_UPDATE:
                message = (
                    f"El SKU '{sku}' ya tiene "
                    f"{existing_physical_stock} unidades físicas "
                    f"en {warehouse.name}. "
                    "La cantidad del archivo se agregará "
                    "como una nueva entrada de inventario."
                )

                warnings.append(message)

                warning_details.append(
                    {
                        "target_field": "inventory.quantity",
                        "message": message,
                    }
                )

        # ======================================================
        # DETERMINAR ESTADO
        # ======================================================

        if errors:
            status = ImportRowResult.STATUS_ERROR

            counters["error_rows"] += 1

        elif warnings:
            status = ImportRowResult.STATUS_WARNING

            counters["warning_rows"] += 1

        else:
            status = ImportRowResult.STATUS_VALID

            counters["valid_rows"] += 1

        # ======================================================
        # DATOS ORIGINALES DEL EXCEL
        # ======================================================

        raw_data = {
            str(header): (source_values[index] if index < len(source_values) else "")
            for index, header in enumerate(headers)
        }

        # ======================================================
        # DATOS NORMALIZADOS
        # ======================================================

        normalized_data = {
            "inventory": {
                "sku": sku,
                "quantity": quantity,
                "reference": data["reference"],
                "notes": data["notes"],
                "warehouse_id": warehouse.pk,
                "warehouse_name": warehouse.name,
                "variant_id": (variant.pk if variant else None),
                "existing_physical_stock": (existing_physical_stock),
            },
            # --------------------------------------------------
            # DIAGNÓSTICO ESTRUCTURADO
            #
            # Esto permite que job_detail identifique:
            #
            # error
            #   ↓
            # inventory.quantity
            #   ↓
            # columna real "Cantidad"
            #   ↓
            # solucionador
            # --------------------------------------------------
            "import_diagnostics": {
                "error_details": (error_details),
                "warning_details": (warning_details),
            },
        }

        # ======================================================
        # RESULTADO DE LA FILA
        # ======================================================

        row_results.append(
            ImportRowResult(
                company=company,
                job=job,
                row_number=row_number,
                status=status,
                raw_data=raw_data,
                normalized_data=normalized_data,
                errors=errors,
                warnings=warnings,
                matched_model=("inventory.ProductVariant" if variant else ""),
                matched_object_id=(str(variant.pk) if variant else ""),
                duplicate_match_data={},
                duplicate_resolution="",
                action_taken="",
            )
        )

    # ==========================================================
    # GUARDAR RESULTADOS
    # ==========================================================

    ImportRowResult.objects.bulk_create(
        row_results,
        batch_size=500,
    )

    return counters


@transaction.atomic
def import_inventory_job(
    *,
    job,
    warehouse,
    headers,
    rows,
    mapping,
    user,
):
    """
    Registra entradas de inventario para una bodega concreta.

    Reglas:
    - Nunca crea productos ni variantes.
    - El SKU debe existir previamente dentro de la empresa.
    - Un mismo SKU puede tener stock en distintas bodegas.
    - La cantidad importada se registra como una entrada adicional.
    - Toda escritura pasa por register_manual_stock_entry().
    """

    company = job.company

    # ==========================================================
    # SEGURIDAD DE EMPRESA / BODEGA
    # ==========================================================

    if not warehouse:
        raise ValidationError("Selecciona una bodega específica.")

    if warehouse.company_id != company.id:
        raise ValidationError("La bodega no pertenece a esta empresa.")

    if not warehouse.is_active:
        raise ValidationError("No puedes importar inventario a una bodega inactiva.")

    # ==========================================================
    # REVALIDACIÓN FINAL
    # ==========================================================

    validation = validate_inventory_rows(
        job=job,
        warehouse=warehouse,
        headers=headers,
        rows=rows,
        mapping=mapping,
    )

    if validation["error_rows"]:
        raise ValidationError(
            (
                "El archivo todavía contiene errores. "
                "Corrígelos antes de importar el inventario."
            )
        )

    # ==========================================================
    # CONTADORES
    # ==========================================================

    imported_rows = 0
    total_quantity = 0

    results = []

    # ==========================================================
    # PROCESAR FILAS
    # ==========================================================

    for fallback_number, row in enumerate(
        rows,
        start=2,
    ):
        row_number = _row_number(
            row,
            fallback_number,
        )

        values = _row_values(row)

        # Ignorar filas totalmente vacías.
        if not any(_text(value) for value in values):
            continue

        data = normalize_inventory_row(
            row=row,
            mapping=mapping,
            row_number=row_number,
        )

        sku = data["sku"]

        # ======================================================
        # OBTENER SKU SOLO DE ESTA EMPRESA
        # ======================================================

        variant = (
            ProductVariant.objects.select_related(
                "product",
                "company",
            )
            .filter(
                company=company,
                product__company=company,
                sku=sku,
                is_active=True,
            )
            .first()
        )

        if not variant:
            raise ValidationError(
                (
                    f"El SKU '{sku}' no existe o no está activo "
                    "en el catálogo de esta empresa."
                )
            )

        if variant.is_bundle:
            raise ValidationError(
                (
                    f"El SKU '{sku}' corresponde a un combo virtual "
                    "y no puede recibir inventario directo."
                )
            )

        # ======================================================
        # CANTIDAD
        # ======================================================

        quantity_raw = data["quantity_raw"]

        try:
            if isinstance(
                quantity_raw,
                bool,
            ):
                raise ValueError

            numeric_value = float(str(quantity_raw).strip())

            if not numeric_value.is_integer():
                raise ValueError

            quantity = int(numeric_value)

        except (
            TypeError,
            ValueError,
            OverflowError,
        ):
            raise ValidationError(
                (f"La cantidad de la fila {row_number} " "debe ser un número entero.")
            )

        if quantity <= 0:
            raise ValidationError(
                (f"La cantidad de la fila {row_number} " "debe ser mayor que cero.")
            )

        # ======================================================
        # REFERENCIA / NOTAS
        # ======================================================

        reference = data["reference"] or f"Importación inventario {job.folio}"

        notes = data["notes"]

        # ======================================================
        # REGISTRAR ENTRADA CON EL SERVICIO OFICIAL
        # ======================================================

        result = register_manual_stock_entry(
            company=company,
            warehouse=warehouse,
            variant=variant,
            quantity=quantity,
            user=user,
            reference=reference,
            notes=notes,
        )

        imported_rows += 1
        total_quantity += quantity

        results.append(
            {
                "row_number": row_number,
                "sku": sku,
                "quantity": quantity,
                "stock_before": result["stock_before"],
                "stock_after": result["stock_after"],
                "movement_id": result["movement"].pk,
            }
        )

    # ==========================================================
    # RESULTADO
    # ==========================================================

    return {
        "imported_rows": imported_rows,
        "total_quantity": total_quantity,
        "warehouse_id": warehouse.pk,
        "warehouse_name": warehouse.name,
        "results": results,
    }
