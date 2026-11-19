import re
import unicodedata

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from purchases.models import Supplier

from data_imports.models import ImportColumnMapping, ImportRowResult


SUPPLIER_TARGET_CHOICES = [
    ("", "Ignorar esta columna"),
    ("supplier.name", "Proveedor · Nombre"),
    ("supplier.legal_name", "Proveedor · Razón social"),
    ("supplier.rfc", "Proveedor · RFC"),
    ("supplier.contact_name", "Proveedor · Persona de contacto"),
    ("supplier.email", "Proveedor · Correo"),
    ("supplier.phone", "Proveedor · Teléfono"),
    ("supplier.whatsapp", "Proveedor · WhatsApp"),
    ("supplier.address", "Proveedor · Dirección"),
    ("supplier.city", "Proveedor · Ciudad / Municipio"),
    ("supplier.state", "Proveedor · Estado"),
    ("supplier.country", "Proveedor · País"),
    ("supplier.payment_terms", "Proveedor · Condiciones de pago"),
    ("supplier.credit_days", "Proveedor · Días de crédito"),
    ("supplier.notes", "Proveedor · Notas"),
    ("supplier.is_active", "Proveedor · Activo"),
]

HEADER_ALIASES = {
    "supplier.name": {"proveedor", "nombre proveedor", "nombre", "supplier", "supplier name"},
    "supplier.legal_name": {"razon social", "razón social", "nombre fiscal", "legal name"},
    "supplier.rfc": {"rfc", "tax id", "identificacion fiscal", "identificación fiscal"},
    "supplier.contact_name": {"contacto", "persona de contacto", "contact name"},
    "supplier.email": {"correo", "email", "correo electronico", "correo electrónico"},
    "supplier.phone": {"telefono", "teléfono", "phone", "tel", "celular"},
    "supplier.whatsapp": {"whatsapp", "wa", "telefono whatsapp", "teléfono whatsapp"},
    "supplier.address": {"direccion", "dirección", "domicilio", "calle", "address"},
    "supplier.city": {"ciudad", "municipio", "alcaldia", "alcaldía", "city"},
    "supplier.state": {"estado", "entidad", "state"},
    "supplier.country": {"pais", "país", "country"},
    "supplier.payment_terms": {"condiciones de pago", "terminos de pago", "términos de pago", "payment terms"},
    "supplier.credit_days": {"dias de credito", "días de crédito", "credito dias", "credit days"},
    "supplier.notes": {"notas", "observaciones", "comentarios", "notes"},
    "supplier.is_active": {"activo", "estatus", "status", "is active"},
}


def _text(value):
    return str(value or "").strip()


def _key(value):
    value = unicodedata.normalize("NFKD", _text(value))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def normalize_rfc(value):
    return re.sub(r"[^A-Z0-9&Ñ]", "", _text(value).upper())


def normalize_phone(value):
    return re.sub(r"\D", "", _text(value))


def suggest_supplier_mapping(headers, company=None):
    choices = dict(SUPPLIER_TARGET_CHOICES)
    if company is not None:
        from custom_fields.services import importable_target_choices
        choices.update(importable_target_choices(company, module="supplier"))

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


def persist_supplier_mapping(*, job, headers, mapping):
    ImportColumnMapping.objects.filter(company=job.company, job=job).delete()
    ImportColumnMapping.objects.bulk_create(
        [
            ImportColumnMapping(
                company=job.company,
                job=job,
                source_column=str(header),
                source_index=index,
                target_field=mapping.get(str(index), ""),
                is_required=mapping.get(str(index)) == "supplier.name",
                confirmed_by_user=True,
            )
            for index, header in enumerate(headers)
        ]
    )


def get_supplier_mapping(job):
    return {
        str(item.source_index): item.target_field
        for item in ImportColumnMapping.objects.filter(
            company=job.company, job=job
        )
    }


def _boolean(value):
    key = _key(value)
    if key in {"", "si", "sí", "yes", "true", "1", "activo"}:
        return True
    if key in {"no", "false", "0", "inactivo"}:
        return False
    raise ValidationError("El valor de Activo debe ser Sí o No.")


def _mapped_row(row, mapping):
    values = row.get("values", []) if isinstance(row, dict) else row
    return {
        target: values[int(index)] if int(index) < len(values) else ""
        for index, target in mapping.items()
        if target
    }


def validate_supplier_rows(
    *,
    job,
    headers,
    rows,
    mapping,
):
    """
    Valida las filas de una importación de proveedores.

    Además de conservar errors y warnings para compatibilidad con
    WMS360, genera un diagnóstico estructurado indicando exactamente
    qué target_field originó cada problema.

    Esto permite que el solucionador de importaciones pueda localizar
    automáticamente la columna real del Excel aunque el archivo tenga
    encabezados personalizados.

    Seguridad:
    - Todos los resultados quedan aislados por company.
    - La búsqueda de duplicados solamente consulta proveedores de
      la empresa actual.
    """

    company = job.company

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

    seen_names = set()

    row_results = []

    # ==========================================================
    # RECORRER FILAS
    # ==========================================================

    for fallback_row_number, raw_row in enumerate(
        rows,
        start=2,
    ):
        row_number = (
            raw_row.get(
                "row_number",
                fallback_row_number,
            )
            if isinstance(raw_row, dict)
            else fallback_row_number
        )

        source_values = (
            raw_row.get(
                "values",
                [],
            )
            if isinstance(raw_row, dict)
            else raw_row
        )

        # ------------------------------------------------------
        # IGNORAR FILAS COMPLETAMENTE VACÍAS
        # ------------------------------------------------------

        if not any(_text(value) for value in source_values):
            continue

        counters["total_rows"] += 1

        # ------------------------------------------------------
        # DATOS ORIGINALES DEL EXCEL
        # ------------------------------------------------------

        raw_data = {
            str(header): (source_values[index] if index < len(source_values) else "")
            for index, header in enumerate(headers)
        }

        # ------------------------------------------------------
        # APLICAR MAPEO
        # ------------------------------------------------------

        mapped = _mapped_row(
            raw_row,
            mapping,
        )

        supplier = {}
        custom_raw = {}

        for target, value in mapped.items():

            if target.startswith("supplier."):
                supplier[target.split(".", 1)[1]] = value

            elif target.startswith("custom."):
                custom_raw[target] = value

        # ======================================================
        # ERRORES Y WARNINGS ESTRUCTURADOS
        # ======================================================

        error_details = []
        warning_details = []

        def add_error(
            target_field,
            message,
        ):
            error_details.append(
                {
                    "target_field": target_field,
                    "message": str(message),
                }
            )

        def add_warning(
            target_field,
            message,
        ):
            warning_details.append(
                {
                    "target_field": target_field,
                    "message": str(message),
                }
            )

        # ======================================================
        # NOMBRE
        # ======================================================

        supplier["name"] = _text(supplier.get("name"))

        if not supplier["name"]:
            add_error(
                "supplier.name",
                "El nombre del proveedor es obligatorio.",
            )

        elif len(supplier["name"]) > 180:
            add_error(
                "supplier.name",
                "El nombre del proveedor supera 180 caracteres.",
            )

        # ======================================================
        # CAMPOS DE TEXTO
        # ======================================================

        text_fields = {
            "legal_name": (
                220,
                "supplier.legal_name",
                "La razón social",
            ),
            "rfc": (
                30,
                "supplier.rfc",
                "El RFC",
            ),
            "contact_name": (
                150,
                "supplier.contact_name",
                "La persona de contacto",
            ),
            "email": (
                254,
                "supplier.email",
                "El correo",
            ),
            "phone": (
                30,
                "supplier.phone",
                "El teléfono",
            ),
            "whatsapp": (
                30,
                "supplier.whatsapp",
                "El WhatsApp",
            ),
            "city": (
                120,
                "supplier.city",
                "La ciudad",
            ),
            "state": (
                120,
                "supplier.state",
                "El estado",
            ),
            "country": (
                120,
                "supplier.country",
                "El país",
            ),
            "payment_terms": (
                160,
                "supplier.payment_terms",
                "Las condiciones de pago",
            ),
        }

        for field_name, (
            limit,
            target_field,
            label,
        ) in text_fields.items():

            supplier[field_name] = _text(supplier.get(field_name))

            if len(supplier[field_name]) > limit:
                add_error(
                    target_field,
                    (f"{label} supera " f"{limit} caracteres."),
                )

        # ======================================================
        # RFC
        # ======================================================

        supplier["rfc"] = normalize_rfc(supplier["rfc"])

        # ======================================================
        # TELÉFONO
        # ======================================================

        supplier["phone"] = normalize_phone(supplier["phone"])

        # ======================================================
        # WHATSAPP
        # ======================================================

        supplier["whatsapp"] = normalize_phone(supplier["whatsapp"])

        # ======================================================
        # EMAIL
        # ======================================================

        if supplier["email"]:

            try:
                validate_email(supplier["email"])

                supplier["email"] = supplier["email"].lower()

            except ValidationError:

                add_warning(
                    "supplier.email",
                    ("Correo inválido; " "se importará vacío."),
                )

                supplier["email"] = ""

        # ======================================================
        # DÍAS DE CRÉDITO
        # ======================================================

        try:
            supplier["credit_days"] = int(_text(supplier.get("credit_days")) or 0)

            if supplier["credit_days"] < 0:
                raise ValueError

        except (
            TypeError,
            ValueError,
        ):
            add_warning(
                "supplier.credit_days",
                ("Días de crédito inválidos; " "se usará 0."),
            )

            supplier["credit_days"] = 0

        # ======================================================
        # ACTIVO / INACTIVO
        # ======================================================

        try:
            supplier["is_active"] = _boolean(supplier.get("is_active"))

        except ValidationError as exc:

            messages = (
                exc.messages
                if getattr(
                    exc,
                    "messages",
                    None,
                )
                else [str(exc)]
            )

            for message in messages:
                add_warning(
                    "supplier.is_active",
                    message,
                )

            supplier["is_active"] = True

        # ======================================================
        # DIRECCIÓN / NOTAS / PAÍS
        # ======================================================

        supplier["address"] = _text(supplier.get("address"))

        supplier["notes"] = _text(supplier.get("notes"))

        supplier["country"] = supplier["country"] or "México"

        # ======================================================
        # CAMPOS PERSONALIZADOS
        # ======================================================

        custom_values = {}

        if not error_details:

            try:
                from custom_fields.services import (
                    normalize_imported_custom_values,
                )

                custom_values = normalize_imported_custom_values(
                    company=company,
                    mapped_data=custom_raw,
                    module="supplier",
                )

            except ValidationError as exc:

                custom_messages = (
                    exc.messages
                    if getattr(
                        exc,
                        "messages",
                        None,
                    )
                    else [str(exc)]
                )

                # Si solamente existe un custom field mapeado,
                # podemos asociar el error exactamente con él.
                custom_targets = list(custom_raw.keys())

                target_field = custom_targets[0] if len(custom_targets) == 1 else ""

                for message in custom_messages:
                    add_error(
                        target_field,
                        message,
                    )

        # ======================================================
        # DUPLICADOS
        # ======================================================

        duplicate = None

        if supplier["name"]:

            # IMPORTANTE:
            # aislamiento absoluto por empresa.
            duplicate = Supplier.objects.filter(
                company=company,
                name__iexact=supplier["name"],
            ).first()

            name_key = _key(supplier["name"])

            if name_key in seen_names:
                add_error(
                    "supplier.name",
                    ("Nombre repetido dentro " "del mismo archivo."),
                )

            seen_names.add(name_key)

        # ======================================================
        # CONVERTIR DIAGNÓSTICOS A FORMATO LEGACY
        # ======================================================

        errors = [item["message"] for item in error_details]

        warnings = [item["message"] for item in warning_details]

        # ======================================================
        # DETERMINAR ESTADO
        # ======================================================

        if errors:

            status = ImportRowResult.STATUS_ERROR

            counters["error_rows"] += 1

        elif duplicate:

            status = ImportRowResult.STATUS_DUPLICATE

            counters["duplicate_rows"] += 1

        elif warnings:

            status = ImportRowResult.STATUS_WARNING

            counters["warning_rows"] += 1

        else:

            status = ImportRowResult.STATUS_VALID

            counters["valid_rows"] += 1

        # ======================================================
        # DATOS NORMALIZADOS + DIAGNÓSTICO
        # ======================================================

        normalized_data = {
            "supplier": supplier,
            "custom": custom_values,
            "import_diagnostics": {
                "error_details": error_details,
                "warning_details": warning_details,
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
                matched_model=("purchases.Supplier" if duplicate else ""),
                matched_object_id=(str(duplicate.pk) if duplicate else ""),
                duplicate_match_data=(
                    {
                        "name": duplicate.name,
                        "rfc": duplicate.rfc,
                        "email": duplicate.email,
                    }
                    if duplicate
                    else {}
                ),
                action_taken="",
            )
        )

    # ==========================================================
    # GUARDADO MASIVO
    # ==========================================================

    ImportRowResult.objects.bulk_create(
        row_results,
        batch_size=500,
    )

    return counters


def validate_customer_rows(
    *,
    job,
    headers,
    rows,
    mapping,
):
    """
    Valida las filas de una importación de clientes.

    Mantiene la lógica actual de:
    - Normalización.
    - Validación.
    - Duplicados contra la base de datos.
    - Duplicados dentro del archivo.
    - Políticas de duplicados.
    - Campos personalizados.

    Además genera import_diagnostics con target_field para que
    el solucionador pueda relacionar cada problema con la columna
    real del archivo importado.

    Seguridad:
    - Todo queda aislado por company.
    - Los clientes existentes se buscan únicamente dentro
      de la empresa del lote.
    """

    company = job.company

    # ==========================================================
    # CLIENTES EXISTENTES DE ESTA EMPRESA
    # ==========================================================

    existing_maps = _build_existing_customer_maps(company)

    # ==========================================================
    # REGENERAR RESULTADOS DEL ANÁLISIS
    # ==========================================================

    ImportRowResult.objects.filter(
        company=company,
        job=job,
    ).delete()

    row_results = []

    # Valores ya encontrados dentro del propio archivo.
    seen_file_values = {
        "rfc": {},
        "email": {},
        "phone": {},
        "whatsapp": {},
    }

    counters = {
        "total_rows": 0,
        "valid_rows": 0,
        "warning_rows": 0,
        "error_rows": 0,
        "duplicate_rows": 0,
    }

    # ==========================================================
    # AUXILIAR PARA DETECTAR TARGET_FIELD
    # ==========================================================

    def detect_target_field(message):
        """
        Convierte un mensaje generado por el normalizador de clientes
        al target lógico correspondiente.

        Es un fallback para errores legacy que todavía llegan como
        simples cadenas.
        """

        text = str(message or "").strip().casefold()

        if not text:
            return ""

        rules = [
            # --------------------------------------------------
            # CAMPOS PERSONALIZADOS
            # --------------------------------------------------
            (
                (
                    "campo personalizado",
                    "campo custom",
                ),
                "",
            ),
            # --------------------------------------------------
            # IDENTIDAD DEL CLIENTE
            # --------------------------------------------------
            (
                (
                    "nombre completo",
                    "nombre del cliente",
                    "nombre es obligatorio",
                ),
                "customer.full_name",
            ),
            (
                ("nombre comercial",),
                "customer.business_name",
            ),
            (
                (
                    "razón social",
                    "razon social",
                ),
                "customer.legal_name",
            ),
            # --------------------------------------------------
            # RFC
            # --------------------------------------------------
            (
                ("rfc",),
                "customer.rfc",
            ),
            # --------------------------------------------------
            # CONTACTO
            # --------------------------------------------------
            (
                (
                    "correo",
                    "email",
                    "e-mail",
                ),
                "customer.email",
            ),
            (
                ("whatsapp",),
                "customer.whatsapp",
            ),
            (
                (
                    "teléfono",
                    "telefono",
                    "celular",
                ),
                "customer.phone",
            ),
            # --------------------------------------------------
            # DATOS COMERCIALES
            # --------------------------------------------------
            (
                ("tipo de cliente",),
                "customer.customer_type",
            ),
            (
                ("origen",),
                "customer.source",
            ),
            (
                (
                    "compra mensual",
                    "compra estimada",
                ),
                "customer.estimated_monthly_purchase",
            ),
            # --------------------------------------------------
            # DIRECCIÓN
            # --------------------------------------------------
            (
                ("calle",),
                "address.street",
            ),
            (
                (
                    "número exterior",
                    "numero exterior",
                ),
                "address.exterior_number",
            ),
            (
                (
                    "número interior",
                    "numero interior",
                ),
                "address.interior_number",
            ),
            (
                ("colonia",),
                "address.neighborhood",
            ),
            (
                (
                    "ciudad",
                    "municipio",
                ),
                "address.city",
            ),
            (
                (
                    "estado",
                    "entidad",
                ),
                "address.state",
            ),
            (
                (
                    "código postal",
                    "codigo postal",
                    "cp inválido",
                    "cp invalido",
                ),
                "address.postal_code",
            ),
            (
                (
                    "país",
                    "pais",
                ),
                "address.country",
            ),
        ]

        for keywords, target_field in rules:
            if any(keyword in text for keyword in keywords):
                return target_field

        return ""

    # ==========================================================
    # RECORRER FILAS
    # ==========================================================

    for fallback_row_number, row in enumerate(
        rows,
        start=2,
    ):
        # ------------------------------------------------------
        # NÚMERO REAL DE FILA
        # ------------------------------------------------------

        if isinstance(row, dict):
            row_number = row.get(
                "row_number",
                fallback_row_number,
            )

            source_values = row.get(
                "values",
                [],
            )

        else:
            row_number = fallback_row_number
            source_values = row

        # ------------------------------------------------------
        # IGNORAR FILAS COMPLETAMENTE VACÍAS
        # ------------------------------------------------------

        if not any(str(value or "").strip() for value in source_values):
            continue

        counters["total_rows"] += 1

        # ======================================================
        # DATOS ORIGINALES
        # ======================================================

        raw_data = {
            str(headers[index]): (
                source_values[index] if index < len(source_values) else ""
            )
            for index in range(len(headers))
        }

        # ======================================================
        # APLICAR MAPEO
        # ======================================================

        mapped_data = {}

        for (
            source_index,
            target_field,
        ) in mapping.items():

            if not target_field:
                continue

            try:
                index = int(source_index)

            except (
                TypeError,
                ValueError,
            ):
                continue

            mapped_data[target_field] = (
                source_values[index] if index < len(source_values) else ""
            )

        # ======================================================
        # NORMALIZAR / VALIDAR CLIENTE
        # ======================================================

        (
            customer,
            address,
            custom_values,
            errors,
            warnings,
        ) = normalize_customer_row(
            mapped_data,
            company=company,
        )

        errors = list(errors or [])

        warnings = list(warnings or [])

        # ======================================================
        # DIAGNÓSTICO ESTRUCTURADO
        # ======================================================

        error_details = []
        warning_details = []

        for message in errors:
            error_details.append(
                {
                    "target_field": (detect_target_field(message)),
                    "message": str(message),
                }
            )

        for message in warnings:
            warning_details.append(
                {
                    "target_field": (detect_target_field(message)),
                    "message": str(message),
                }
            )

        # ======================================================
        # DUPLICADOS CONTRA LA BASE DE DATOS
        # ======================================================

        duplicate_messages = []

        duplicate_details = []

        duplicate_match_data = {}

        (
            existing_customer,
            existing_fields,
            conflicting_customers,
        ) = _find_existing_customer_matches(
            customer,
            existing_maps,
        )

        # ------------------------------------------------------
        # IDENTIFICADORES QUE APUNTAN A CLIENTES DIFERENTES
        # ------------------------------------------------------

        if conflicting_customers:

            conflict_message = (
                "Los identificadores del archivo coinciden "
                "con clientes diferentes de esta empresa. "
                "Revisión manual obligatoria."
            )

            errors.append(conflict_message)

            # En este caso puede haber más de un identificador
            # involucrado, por lo que registramos cada campo.
            conflict_fields = []

            for field_name in (
                "rfc",
                "email",
                "phone",
                "whatsapp",
            ):
                if customer.get(field_name):
                    conflict_fields.append(field_name)

            if conflict_fields:

                for field_name in conflict_fields:
                    error_details.append(
                        {
                            "target_field": (f"customer.{field_name}"),
                            "message": (conflict_message),
                        }
                    )

            else:
                error_details.append(
                    {
                        "target_field": "",
                        "message": (conflict_message),
                    }
                )

        # ------------------------------------------------------
        # CLIENTE YA EXISTENTE
        # ------------------------------------------------------

        elif existing_customer:

            field_text = ", ".join(existing_fields)

            duplicate_message = (
                "Coincide con el cliente "
                f"#{existing_customer.pk} "
                f'"{existing_customer.full_name}" '
                f"por {field_text}."
            )

            duplicate_messages.append(duplicate_message)

            duplicate_match_data = {
                "source": "database",
                "matched_customer_id": (existing_customer.pk),
                "matched_fields": (existing_fields),
            }

            # Cada identificador coincidente queda relacionado
            # con su campo real.
            for field_name in existing_fields:

                target_field = (
                    f"customer.{field_name}"
                    if field_name
                    in {
                        "rfc",
                        "email",
                        "phone",
                        "whatsapp",
                    }
                    else ""
                )

                duplicate_details.append(
                    {
                        "target_field": (target_field),
                        "message": (duplicate_message),
                    }
                )

        # ======================================================
        # DUPLICADOS DENTRO DEL MISMO ARCHIVO
        # ======================================================

        file_matches = {}

        for field_name in (
            "rfc",
            "email",
            "phone",
            "whatsapp",
        ):
            value = customer.get(field_name)

            if not value:
                continue

            previous = seen_file_values[field_name].get(value)

            if previous:

                file_matches.setdefault(
                    previous["row_number"],
                    [],
                ).append(field_name)

            else:

                seen_file_values[field_name][value] = {
                    "row_number": (row_number),
                }

        # ------------------------------------------------------
        # COINCIDENCIA CON OTRA FILA DEL ARCHIVO
        # ------------------------------------------------------

        if file_matches and not existing_customer:

            (
                matched_row_number,
                matched_fields,
            ) = sorted(
                file_matches.items(),
                key=lambda item: (
                    -len(item[1]),
                    item[0],
                ),
            )[0]

            duplicate_message = (
                "Coincide con la fila "
                f"{matched_row_number} "
                "del archivo por " + ", ".join(matched_fields) + "."
            )

            duplicate_messages.append(duplicate_message)

            duplicate_match_data = {
                "source": "file",
                "matched_row_number": (matched_row_number),
                "matched_fields": (matched_fields),
            }

            for field_name in matched_fields:

                duplicate_details.append(
                    {
                        "target_field": (f"customer.{field_name}"),
                        "message": (duplicate_message),
                    }
                )

        # ======================================================
        # DUPLICADOS TAMBIÉN SON DIAGNÓSTICOS
        # ======================================================

        warning_details.extend(duplicate_details)

        # ======================================================
        # DETERMINAR ESTADO
        # ======================================================

        if errors:

            status = ImportRowResult.STATUS_ERROR

            counters["error_rows"] += 1

            duplicate_resolution = ""

        elif duplicate_messages:

            status = ImportRowResult.STATUS_DUPLICATE

            counters["duplicate_rows"] += 1

            duplicate_resolution = _automatic_duplicate_resolution(job)

        elif warnings:

            status = ImportRowResult.STATUS_WARNING

            counters["warning_rows"] += 1

            duplicate_resolution = ""

        else:

            status = ImportRowResult.STATUS_VALID

            counters["valid_rows"] += 1

            duplicate_resolution = ""

        # ======================================================
        # DATOS NORMALIZADOS
        # ======================================================

        normalized_data = {
            "customer": customer,
            "address": address,
            "custom_fields": custom_values,
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
                normalized_data=(normalized_data),
                errors=errors,
                warnings=(warnings + duplicate_messages),
                matched_model=("crm.Customer" if existing_customer else ""),
                matched_object_id=(
                    str(existing_customer.pk) if existing_customer else ""
                ),
                duplicate_match_data=(duplicate_match_data),
                duplicate_resolution=(duplicate_resolution),
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
