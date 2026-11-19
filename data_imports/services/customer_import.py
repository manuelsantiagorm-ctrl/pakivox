import re
import unicodedata
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from crm.models import Customer

from data_imports.models import (
    ImportColumnMapping,
    ImportRowResult,
)


CUSTOMER_TARGET_CHOICES = [
    ("", "Ignorar esta columna"),
    ("customer.full_name", "Cliente · Nombre completo / Razón social"),
    ("customer.business_name", "Cliente · Nombre comercial"),
    ("customer.rfc", "Cliente · RFC"),
    ("customer.email", "Cliente · Correo"),
    ("customer.phone", "Cliente · Teléfono"),
    ("customer.whatsapp", "Cliente · WhatsApp"),
    ("customer.customer_type", "Cliente · Tipo"),
    ("customer.source", "Cliente · Origen"),
    ("customer.notes", "Cliente · Notas"),
    (
        "customer.estimated_monthly_purchase",
        "Cliente · Compra mensual estimada",
    ),
    ("customer.is_active", "Cliente · Activo"),
    ("address.address_type", "Dirección · Tipo"),
    ("address.street", "Dirección · Calle"),
    ("address.exterior_number", "Dirección · Número exterior"),
    ("address.interior_number", "Dirección · Número interior"),
    ("address.neighborhood", "Dirección · Colonia"),
    ("address.city", "Dirección · Ciudad"),
    ("address.municipality", "Dirección · Municipio / Alcaldía"),
    ("address.state", "Dirección · Estado"),
    ("address.country", "Dirección · País"),
    ("address.zip_code", "Dirección · Código postal"),
    ("address.is_default", "Dirección · Principal"),
]

CUSTOMER_FIELD_POLICIES = {
    "customer.full_name": {
        "label": "Cliente · Nombre completo / Razón social",
        "level": "required",
        "can_ignore": False,
        "suggested_default": "",
        "placeholder": "Debes relacionar una columna del archivo",
    },
    "customer.business_name": {
        "label": "Cliente · Nombre comercial",
        "level": "optional",
        "can_ignore": True,
        "suggested_default": "",
        "placeholder": "Ej. Comercializadora Centro",
    },
    "customer.rfc": {
        "label": "Cliente · RFC",
        "level": "optional",
        "can_ignore": True,
        "suggested_default": "",
        "placeholder": "Ej. XAXX010101000",
    },
    "customer.email": {
        "label": "Cliente · Correo",
        "level": "recommended",
        "can_ignore": True,
        "suggested_default": "",
        "placeholder": "Ej. ventas@cliente.com",
    },
    "customer.phone": {
        "label": "Cliente · Teléfono",
        "level": "recommended",
        "can_ignore": True,
        "suggested_default": "",
        "placeholder": "Ej. 5512345678",
    },
    "customer.whatsapp": {
        "label": "Cliente · WhatsApp",
        "level": "recommended",
        "can_ignore": True,
        "suggested_default": "",
        "placeholder": "Ej. 525512345678",
    },
    "customer.customer_type": {
        "label": "Cliente · Tipo",
        "level": "optional",
        "can_ignore": True,
        "suggested_default": "Cliente",
        "placeholder": "Ej. Cliente",
    },
    "customer.source": {
        "label": "Cliente · Origen",
        "level": "optional",
        "can_ignore": True,
        "suggested_default": "Manual",
        "placeholder": "Ej. WhatsApp, Sitio web, Manual",
    },
    "customer.notes": {
        "label": "Cliente · Notas",
        "level": "optional",
        "can_ignore": True,
        "suggested_default": "",
        "placeholder": "Texto opcional para todas las filas",
    },
    "customer.estimated_monthly_purchase": {
        "label": "Cliente · Compra mensual estimada",
        "level": "optional",
        "can_ignore": True,
        "suggested_default": "",
        "placeholder": "Ej. 25000",
    },
    "customer.is_active": {
        "label": "Cliente · Activo",
        "level": "optional",
        "can_ignore": True,
        "suggested_default": "Sí",
        "placeholder": "Sí / No",
    },
    "address.address_type": {
        "label": "Dirección · Tipo",
        "level": "optional",
        "can_ignore": True,
        "suggested_default": "Envío",
        "placeholder": "Ej. Envío",
    },
    "address.street": {
        "label": "Dirección · Calle",
        "level": "optional",
        "can_ignore": True,
        "suggested_default": "",
        "placeholder": "Ej. Av. Reforma",
    },
    "address.exterior_number": {
        "label": "Dirección · Número exterior",
        "level": "optional",
        "can_ignore": True,
        "suggested_default": "",
        "placeholder": "Ej. 120",
    },
    "address.interior_number": {
        "label": "Dirección · Número interior",
        "level": "optional",
        "can_ignore": True,
        "suggested_default": "",
        "placeholder": "Ej. 4B",
    },
    "address.neighborhood": {
        "label": "Dirección · Colonia",
        "level": "optional",
        "can_ignore": True,
        "suggested_default": "",
        "placeholder": "Ej. Roma Norte",
    },
    "address.city": {
        "label": "Dirección · Ciudad",
        "level": "optional",
        "can_ignore": True,
        "suggested_default": "",
        "placeholder": "Ej. Ciudad de México",
    },
    "address.municipality": {
        "label": "Dirección · Municipio / Alcaldía",
        "level": "optional",
        "can_ignore": True,
        "suggested_default": "",
        "placeholder": "Ej. Cuauhtémoc",
    },
    "address.state": {
        "label": "Dirección · Estado",
        "level": "optional",
        "can_ignore": True,
        "suggested_default": "",
        "placeholder": "Ej. Jalisco",
    },
    "address.country": {
        "label": "Dirección · País",
        "level": "optional",
        "can_ignore": True,
        "suggested_default": "México",
        "placeholder": "Ej. México",
    },
    "address.zip_code": {
        "label": "Dirección · Código postal",
        "level": "optional",
        "can_ignore": True,
        "suggested_default": "",
        "placeholder": "Ej. 06000",
    },
    "address.is_default": {
        "label": "Dirección · Principal",
        "level": "optional",
        "can_ignore": True,
        "suggested_default": "Sí",
        "placeholder": "Sí / No",
    },
}

CUSTOMER_MAPPING_DEFAULTS_OPTION_KEY = "customer_mapping_defaults"


def get_customer_mapping_defaults(job):
    options = job.options if isinstance(job.options, dict) else {}
    raw_defaults = options.get(CUSTOMER_MAPPING_DEFAULTS_OPTION_KEY, {})
    if not isinstance(raw_defaults, dict):
        return {}

    return {
        str(target): str(value).strip()
        for target, value in raw_defaults.items()
        if target in CUSTOMER_FIELD_POLICIES
        and CUSTOMER_FIELD_POLICIES[target]["can_ignore"]
        and str(value or "").strip()
    }


def sanitize_customer_mapping_defaults(*, values, mapping):
    """
    Conserva únicamente defaults permitidos y evita que un valor global
    sustituya silenciosamente un campo obligatorio.
    """
    mapped_targets = {
        str(target)
        for target in (mapping or {}).values()
        if target
    }
    cleaned = {}

    for target, policy in CUSTOMER_FIELD_POLICIES.items():
        if not policy["can_ignore"]:
            continue

        raw_value = (values or {}).get(target, "")
        value = str(raw_value or "").strip()

        # Si la columna ya está mapeada, su valor por fila tiene prioridad.
        # No persistimos un default oculto que el usuario no verá como faltante.
        if target in mapped_targets or not value:
            continue

        cleaned[target] = value[:1000]

    return cleaned


def apply_customer_mapping_defaults(mapped_data, defaults):
    """
    Aplica valores globales solo cuando el campo no llegó mapeado o está vacío.
    No sobreescribe datos reales de una fila.
    """
    result = dict(mapped_data or {})

    for target, value in (defaults or {}).items():
        if target not in CUSTOMER_FIELD_POLICIES:
            continue
        if not CUSTOMER_FIELD_POLICIES[target]["can_ignore"]:
            continue

        current = result.get(target)
        if current is None or str(current).strip() == "":
            result[target] = value

    return result


def build_customer_mapping_analysis(*, headers, mapping, defaults=None):
    """
    Resumen determinista del archivo para la pantalla de mapeo inteligente.
    """
    mapping = mapping or {}
    defaults = defaults or {}

    mapped_targets = {
        str(target)
        for target in mapping.values()
        if target
    }

    ignored_headers = []
    mapped_headers = []

    for index, header in enumerate(headers or []):
        target = mapping.get(str(index), "")
        if target:
            mapped_headers.append(
                {
                    "header": str(header),
                    "target": target,
                    "target_label": CUSTOMER_FIELD_POLICIES.get(
                        target,
                        {},
                    ).get("label", target),
                }
            )
        else:
            ignored_headers.append(str(header))

    missing_fields = []
    for target, policy in CUSTOMER_FIELD_POLICIES.items():
        if target in mapped_targets:
            continue

        missing_fields.append(
            {
                "target": target,
                "label": policy["label"],
                "level": policy["level"],
                "can_ignore": policy["can_ignore"],
                "value": defaults.get(target, ""),
                "suggested_default": policy["suggested_default"],
                "placeholder": policy["placeholder"],
            }
        )

    return {
        "total_columns": len(headers or []),
        "mapped_columns": len(mapped_headers),
        "ignored_columns": len(ignored_headers),
        "mapped_headers": mapped_headers,
        "ignored_headers": ignored_headers,
        "missing_fields": missing_fields,
        "missing_required": [
            item for item in missing_fields
            if item["level"] == "required"
        ],
        "missing_recommended": [
            item for item in missing_fields
            if item["level"] == "recommended"
        ],
        "missing_optional": [
            item for item in missing_fields
            if item["level"] == "optional"
        ],
        "ready_for_validation": not any(
            item["level"] == "required"
            for item in missing_fields
        ),
    }



HEADER_ALIASES = {
    "customer.full_name": {
        "cliente",
        "nombre",
        "nombre completo",
        "nombre completo razon social",
        "nombre completo / razon social",
        "nombre completo razón social",
        "nombre completo / razón social",
        "razon social",
        "razón social",
        "cliente razon social",
        "cliente razón social",
        "empresa",
        "nombre cliente",
        "customer",
        "customer name",
        "full name",
    },
    "customer.business_name": {
        "nombre comercial",
        "negocio",
        "empresa comercial",
        "business name",
        "trade name",
    },
    "customer.rfc": {
        "rfc",
        "tax id",
        "tax identification",
        "identificacion fiscal",
        "identificación fiscal",
    },
    "customer.email": {
        "email",
        "correo",
        "correo electronico",
        "correo electrónico",
        "e mail",
    },
    "customer.phone": {
        "telefono",
        "teléfono",
        "telefono fijo",
        "tel",
        "phone",
        "celular",
        "movil",
        "móvil",
    },
    "customer.whatsapp": {
        "whatsapp",
        "telefono whatsapp",
        "teléfono whatsapp",
        "numero whatsapp",
        "número whatsapp",
        "wa",
    },
    "customer.customer_type": {
        "tipo",
        "tipo cliente",
        "tipo de cliente",
        "segmento",
        "customer type",
    },
    "customer.source": {
        "origen",
        "fuente",
        "source",
        "como llego",
        "cómo llegó",
    },
    "customer.notes": {
        "notas",
        "observaciones",
        "comentarios",
        "notes",
    },
    "customer.estimated_monthly_purchase": {
        "compra mensual estimada",
        "compra mensual",
        "venta mensual estimada",
        "potencial mensual",
        "estimated monthly purchase",
    },
    "customer.is_active": {
        "activo",
        "estatus",
        "status",
        "is active",
    },
    "address.address_type": {
        "tipo direccion",
        "tipo dirección",
        "address type",
    },
    "address.street": {
        "calle",
        "direccion",
        "dirección",
        "domicilio",
        "street",
    },
    "address.exterior_number": {
        "numero exterior",
        "número exterior",
        "no exterior",
        "num ext",
        "exterior",
    },
    "address.interior_number": {
        "numero interior",
        "número interior",
        "no interior",
        "num int",
        "interior",
    },
    "address.neighborhood": {
        "colonia",
        "barrio",
        "neighborhood",
    },
    "address.city": {
        "ciudad",
        "city",
    },
    "address.municipality": {
        "municipio",
        "alcaldia",
        "alcaldía",
        "municipality",
    },
    "address.state": {
        "estado",
        "entidad",
        "state",
    },
    "address.country": {
        "pais",
        "país",
        "country",
    },
    "address.zip_code": {
        "codigo postal",
        "código postal",
        "cp",
        "zip",
        "zip code",
        "postal code",
    },
    "address.is_default": {
        "direccion principal",
        "dirección principal",
        "predeterminada",
        "is default",
    },
}


CUSTOMER_TYPE_ALIASES = {
    "lead": "lead",
    "prospecto": "prospect",
    "prospect": "prospect",
    "cliente": "customer",
    "customer": "customer",
    "vip": "vip",
    "cliente vip": "vip",
    "distribuidor": "distributor",
    "distributor": "distributor",
}


SOURCE_ALIASES = {
    "whatsapp": "whatsapp",
    "facebook": "facebook",
    "instagram": "instagram",
    "tiktok": "tiktok",
    "sitio web": "website",
    "website": "website",
    "web": "website",
    "recomendacion": "referral",
    "recomendación": "referral",
    "referido": "referral",
    "referral": "referral",
    "marketplace": "marketplace",
    "manual": "manual",
    "excel": "manual",
}


ADDRESS_TYPE_ALIASES = {
    "facturacion": "billing",
    "facturación": "billing",
    "billing": "billing",
    "envio": "shipping",
    "envío": "shipping",
    "shipping": "shipping",
    "bodega": "warehouse",
    "almacen": "warehouse",
    "almacén": "warehouse",
    "warehouse": "warehouse",
}


TRUE_VALUES = {
    "1",
    "si",
    "sí",
    "true",
    "verdadero",
    "activo",
    "active",
    "yes",
}


FALSE_VALUES = {
    "0",
    "no",
    "false",
    "falso",
    "inactivo",
    "inactive",
}


INVALID_NAME_MARKERS = {
    "vacio",
    "vacío",
    "n a",
    "na",
    "null",
    "sin nombre",
    "ninguno",
    "-",
}


def normalize_text(value):
    value = str(value or "").strip().lower()

    value = unicodedata.normalize(
        "NFKD",
        value,
    )

    value = "".join(
        character
        for character in value
        if not unicodedata.combining(character)
    )

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value,
    )

    return " ".join(
        value.split()
    )


def normalize_phone(value):
    digits = "".join(
        character
        for character in str(value or "")
        if character.isdigit()
    )

    if not digits:
        return ""

    if len(digits) == 10:
        return f"52{digits}"

    if len(digits) == 12 and digits.startswith("52"):
        return digits

    if len(digits) == 13 and digits.startswith("521"):
        return digits

    return digits


def normalize_rfc(value):
    return re.sub(
        r"\s+",
        "",
        str(value or "").upper(),
    )


def normalize_email(value):
    return str(value or "").strip().lower()


def normalize_bool(value, default=True):
    normalized = normalize_text(value)

    if not normalized:
        return default

    normalized_true_values = {
        normalize_text(item)
        for item in TRUE_VALUES
    }

    normalized_false_values = {
        normalize_text(item)
        for item in FALSE_VALUES
    }

    if normalized in normalized_true_values:
        return True

    if normalized in normalized_false_values:
        return False

    raise ValueError(
        f'No se reconoce el valor booleano "{value}".'
    )


def normalize_decimal(value, default=Decimal("0")):
    if value in {
        None,
        "",
    }:
        return default

    text = str(value).strip()

    text = text.replace(
        "$",
        "",
    ).replace(
        "MXN",
        "",
    ).replace(
        "mxn",
        "",
    ).replace(
        ",",
        "",
    )

    try:
        result = Decimal(text)
    except InvalidOperation as error:
        raise ValueError(
            f'No se reconoce el importe "{value}".'
        ) from error

    if result < 0:
        raise ValueError(
            "La compra mensual estimada no puede ser negativa."
        )

    return result.quantize(
        Decimal("0.01")
    )


def suggest_mapping(headers, company=None):
    normalized_aliases = {}

    for target_field, aliases in HEADER_ALIASES.items():
        normalized_aliases[target_field] = {
            normalize_text(alias)
            for alias in aliases
        }

    if company is not None:
        from custom_fields.models import CustomFieldDefinition
        for definition in CustomFieldDefinition.objects.filter(
            company=company,
            is_active=True,
            allow_import=True,
        ):
            normalized_aliases[f"custom.{definition.pk}"] = {
                normalize_text(definition.name),
                normalize_text(definition.slug),
            }

    proposed = {}

    for index, header in enumerate(headers):
        normalized_header = normalize_text(header)
        selected_target = ""

        for target_field, aliases in normalized_aliases.items():
            if normalized_header in aliases:
                selected_target = target_field
                break

        if not selected_target:
            best_target = ""
            best_score = 0

            header_tokens = set(
                normalized_header.split()
            )

            for target_field, aliases in normalized_aliases.items():
                for alias in aliases:
                    alias_tokens = set(
                        alias.split()
                    )

                    if not header_tokens or not alias_tokens:
                        continue

                    score = len(
                        header_tokens & alias_tokens
                    ) / len(
                        header_tokens | alias_tokens
                    )

                    if score > best_score:
                        best_score = score
                        best_target = target_field

            if best_score >= 0.75:
                selected_target = best_target

        proposed[str(index)] = selected_target

    return proposed


def persist_mapping(
    *,
    job,
    headers,
    mapping,
):
    ImportColumnMapping.objects.filter(
        company=job.company,
        job=job,
    ).delete()

    rows = []

    for index, header in enumerate(headers):
        target_field = mapping.get(
            str(index),
            "",
        )

        rows.append(
            ImportColumnMapping(
                company=job.company,
                job=job,
                source_column=str(header),
                source_index=index,
                target_field=target_field,
                is_required=(
                    target_field == "customer.full_name"
                ),
                confidence=(
                    Decimal("100.00")
                    if target_field
                    else Decimal("0.00")
                ),
                confirmed_by_user=True,
            )
        )

    ImportColumnMapping.objects.bulk_create(
        rows
    )


def get_saved_mapping(job):
    return {
        str(mapping.source_index): mapping.target_field
        for mapping in (
            ImportColumnMapping.objects
            .filter(
                company=job.company,
                job=job,
            )
            .order_by(
                "source_index"
            )
        )
    }


def _build_existing_customer_maps(company):
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
        .only(
            "id",
            "rfc",
            "email",
            "phone",
            "whatsapp",
            "full_name",
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


def _find_existing_customer_matches(
    normalized_customer,
    existing_maps,
):
    checks = (
        ("rfc", normalized_customer.get("rfc")),
        ("email", normalized_customer.get("email")),
        ("whatsapp", normalized_customer.get("whatsapp")),
        ("phone", normalized_customer.get("phone")),
    )

    matches = {}

    for field_name, value in checks:
        if not value:
            continue

        customer = existing_maps[field_name].get(value)

        if not customer:
            continue

        item = matches.setdefault(
            customer.pk,
            {
                "customer": customer,
                "fields": [],
            },
        )
        item["fields"].append(field_name)

    if not matches:
        return None, [], False

    if len(matches) > 1:
        return None, [], True

    match = next(iter(matches.values()))
    return match["customer"], match["fields"], False

def _normalize_customer_type(value):
    normalized = normalize_text(value)

    if not normalized:
        return "customer"

    result = CUSTOMER_TYPE_ALIASES.get(
        normalized
    )

    if not result:
        raise ValueError(
            f'El tipo de cliente "{value}" no es válido.'
        )

    return result


def _normalize_source(value):
    normalized = normalize_text(value)

    if not normalized:
        return "manual"

    result = SOURCE_ALIASES.get(
        normalized
    )

    if not result:
        raise ValueError(
            f'El origen "{value}" no es válido.'
        )

    return result


def _normalize_address_type(value):
    normalized = normalize_text(value)

    if not normalized:
        return "shipping"

    result = ADDRESS_TYPE_ALIASES.get(
        normalized
    )

    if not result:
        raise ValueError(
            f'El tipo de dirección "{value}" no es válido.'
        )

    return result


def normalize_customer_row(mapped_data, company=None):
    errors = []
    warnings = []

    customer = {
        "full_name": str(
            mapped_data.get(
                "customer.full_name",
                "",
            )
            or ""
        ).strip(),
        "business_name": str(
            mapped_data.get(
                "customer.business_name",
                "",
            )
            or ""
        ).strip(),
        "rfc": normalize_rfc(
            mapped_data.get(
                "customer.rfc",
                "",
            )
        ),
        "email": normalize_email(
            mapped_data.get(
                "customer.email",
                "",
            )
        ),
        "phone": normalize_phone(
            mapped_data.get(
                "customer.phone",
                "",
            )
        ),
        "whatsapp": normalize_phone(
            mapped_data.get(
                "customer.whatsapp",
                "",
            )
        ),
        "notes": str(
            mapped_data.get(
                "customer.notes",
                "",
            )
            or ""
        ).strip(),
    }

    normalized_name = normalize_text(customer["full_name"])

    if normalized_name in {normalize_text(item) for item in INVALID_NAME_MARKERS}:
        customer["full_name"] = ""

    if not customer["full_name"]:
        errors.append(
            "Falta el nombre completo o razón social."
        )

    if len(customer["full_name"]) > 200:
        errors.append(
            "El nombre completo supera 200 caracteres."
        )

    if len(customer["business_name"]) > 200:
        errors.append(
            "El nombre comercial supera 200 caracteres."
        )

    if len(customer["rfc"]) > 20:
        errors.append(
            "El RFC supera 20 caracteres."
        )

    if customer["email"]:
        try:
            validate_email(
                customer["email"]
            )
        except ValidationError:
            errors.append(
                f'El correo "{customer["email"]}" no es válido.'
            )

    if customer["phone"] and len(customer["phone"]) < 10:
        errors.append(
            "El teléfono debe contener al menos 10 dígitos."
        )

    if customer["whatsapp"] and len(customer["whatsapp"]) < 10:
        errors.append(
            "El WhatsApp debe contener al menos 10 dígitos."
        )

    if not customer["phone"] and customer["whatsapp"]:
        customer["phone"] = customer["whatsapp"]

    if not customer["whatsapp"] and customer["phone"]:
        customer["whatsapp"] = customer["phone"]

    try:
        customer["customer_type"] = _normalize_customer_type(
            mapped_data.get(
                "customer.customer_type",
                "",
            )
        )
    except ValueError as error:
        errors.append(
            str(error)
        )
        customer["customer_type"] = "customer"

    try:
        customer["source"] = _normalize_source(
            mapped_data.get(
                "customer.source",
                "",
            )
        )
    except ValueError as error:
        errors.append(
            str(error)
        )
        customer["source"] = "manual"

    try:
        customer["estimated_monthly_purchase"] = str(
            normalize_decimal(
                mapped_data.get(
                    "customer.estimated_monthly_purchase",
                    "",
                )
            )
        )
    except ValueError as error:
        errors.append(
            str(error)
        )
        customer["estimated_monthly_purchase"] = "0.00"

    try:
        customer["is_active"] = normalize_bool(
            mapped_data.get(
                "customer.is_active",
                "",
            ),
            default=True,
        )
    except ValueError as error:
        errors.append(
            str(error)
        )
        customer["is_active"] = True

    address = {
        "address_type": "",
        "street": str(
            mapped_data.get(
                "address.street",
                "",
            )
            or ""
        ).strip(),
        "exterior_number": str(
            mapped_data.get(
                "address.exterior_number",
                "",
            )
            or ""
        ).strip(),
        "interior_number": str(
            mapped_data.get(
                "address.interior_number",
                "",
            )
            or ""
        ).strip(),
        "neighborhood": str(
            mapped_data.get(
                "address.neighborhood",
                "",
            )
            or ""
        ).strip(),
        "city": str(
            mapped_data.get(
                "address.city",
                "",
            )
            or ""
        ).strip(),
        "municipality": str(
            mapped_data.get(
                "address.municipality",
                "",
            )
            or ""
        ).strip(),
        "state": str(
            mapped_data.get(
                "address.state",
                "",
            )
            or ""
        ).strip(),
        "country": str(
            mapped_data.get(
                "address.country",
                "",
            )
            or "México"
        ).strip(),
        "zip_code": str(
            mapped_data.get(
                "address.zip_code",
                "",
            )
            or ""
        ).strip(),
    }

    address_has_data = any(
        (
            address["street"],
            address["exterior_number"],
            address["interior_number"],
            address["neighborhood"],
            address["city"],
            address["municipality"],
            address["state"],
            address["zip_code"],
            str(
                mapped_data.get(
                    "address.address_type",
                    "",
                )
                or ""
            ).strip(),
        )
    )

    if address_has_data:
        try:
            address["address_type"] = _normalize_address_type(
                mapped_data.get(
                    "address.address_type",
                    "",
                )
            )
        except ValueError as error:
            errors.append(
                str(error)
            )
            address["address_type"] = "shipping"

        try:
            address["is_default"] = normalize_bool(
                mapped_data.get(
                    "address.is_default",
                    "",
                ),
                default=True,
            )
        except ValueError as error:
            errors.append(
                str(error)
            )
            address["is_default"] = True

        missing_address_fields = []

        for field_name, label in (
            ("street", "calle"),
            ("city", "ciudad"),
            ("state", "estado"),
            ("zip_code", "código postal"),
        ):
            if not address[field_name]:
                missing_address_fields.append(
                    label
                )

        if missing_address_fields:
            address["is_complete"] = False
            address["missing_fields"] = missing_address_fields

            warnings.append(
                "La dirección está incompleta. Faltan: "
                + ", ".join(missing_address_fields)
                + ". El cliente sí puede importarse, "
                "pero esta dirección será omitida."
            )
        else:
            address["is_complete"] = True
            address["missing_fields"] = []

    else:
        address = {}

    custom_values = {}
    if company is not None:
        try:
            from custom_fields.services import normalize_imported_custom_values
            custom_values = normalize_imported_custom_values(
                company=company,
                mapped_data=mapped_data,
            )
        except ValidationError as error:
            errors.extend(error.messages)

    if not customer["rfc"] and not customer["email"]:
        if not customer["phone"] and not customer["whatsapp"]:
            warnings.append(
                "No hay RFC, correo ni teléfono para detectar duplicados."
            )

    return customer, address, custom_values, errors, warnings


def _automatic_duplicate_resolution(job):
    if job.duplicate_policy == job.DUPLICATE_SKIP:
        return ImportRowResult.RESOLUTION_SKIP

    if job.duplicate_policy == job.DUPLICATE_UPDATE:
        return ImportRowResult.RESOLUTION_UPDATE_ADDRESS

    if job.duplicate_policy == job.DUPLICATE_CREATE:
        return ImportRowResult.RESOLUTION_CREATE_NEW

    return ""


def validate_customer_rows(
    *,
    job,
    headers,
    rows,
    mapping,
):
    existing_maps = _build_existing_customer_maps(
        job.company
    )

    ImportRowResult.objects.filter(
        company=job.company,
        job=job,
    ).delete()

    row_results = []

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

    mapping_defaults = get_customer_mapping_defaults(job)

    for row in rows:
        counters["total_rows"] += 1

        source_values = row["values"]

        raw_data = {
            str(headers[index]): (
                source_values[index]
                if index < len(source_values)
                else ""
            )
            for index in range(len(headers))
        }

        mapped_data = {}

        for source_index, target_field in mapping.items():
            if not target_field:
                continue

            try:
                index = int(source_index)
            except (TypeError, ValueError):
                continue

            mapped_data[target_field] = (
                source_values[index]
                if index < len(source_values)
                else ""
            )

        mapped_data = apply_customer_mapping_defaults(
            mapped_data,
            mapping_defaults,
        )

        customer, address, custom_values, errors, warnings = normalize_customer_row(
            mapped_data,
            company=job.company,
        )

        duplicate_messages = []
        duplicate_match_data = {}

        existing_customer, existing_fields, conflicting_customers = (
            _find_existing_customer_matches(
                customer,
                existing_maps,
            )
        )

        if conflicting_customers:
            errors.append(
                "Los identificadores del archivo coinciden con clientes "
                "diferentes de esta empresa. Revisión manual obligatoria."
            )

        elif existing_customer:
            field_text = ", ".join(existing_fields)
            duplicate_messages.append(
                "Coincide con el cliente "
                f'#{existing_customer.pk} "{existing_customer.full_name}" '
                f"por {field_text}."
            )
            duplicate_match_data = {
                "source": "database",
                "matched_customer_id": existing_customer.pk,
                "matched_fields": existing_fields,
            }

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
                    "row_number": row["row_number"],
                }

        if file_matches and not existing_customer:
            matched_row_number, matched_fields = sorted(
                file_matches.items(),
                key=lambda item: (-len(item[1]), item[0]),
            )[0]

            duplicate_messages.append(
                "Coincide con la fila "
                f"{matched_row_number} del archivo por "
                + ", ".join(matched_fields)
                + "."
            )
            duplicate_match_data = {
                "source": "file",
                "matched_row_number": matched_row_number,
                "matched_fields": matched_fields,
            }

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

        normalized_data = {
            "customer": customer,
            "address": address,
            "custom_fields": custom_values,
        }

        row_results.append(
            ImportRowResult(
                company=job.company,
                job=job,
                row_number=row["row_number"],
                status=status,
                raw_data=raw_data,
                normalized_data=normalized_data,
                errors=errors,
                warnings=(warnings + duplicate_messages),
                matched_model=(
                    "crm.Customer"
                    if existing_customer
                    else ""
                ),
                matched_object_id=(
                    str(existing_customer.pk)
                    if existing_customer
                    else ""
                ),
                duplicate_match_data=duplicate_match_data,
                duplicate_resolution=duplicate_resolution,
                action_taken="",
            )
        )

    ImportRowResult.objects.bulk_create(
        row_results,
        batch_size=500,
    )

    return counters
