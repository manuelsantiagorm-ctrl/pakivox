from dataclasses import dataclass

from django.core.exceptions import ValidationError

from warehouse.models import ShippingOrder

from shipping_hub.models import (
    CarrierShipment,
    ShippingOriginAddress,
)
from shipping_hub.strategies.resolver import (
    get_primary_shipping_integration,
)

# ============================================================
# CONTEXTO DE CUBICACIÓN
# ============================================================


@dataclass
class CubicationContext:
    """
    Datos preparados para la Mesa de Cubicación.

    El contexto es independiente del proveedor logístico:
    puede utilizar Envia, Estafeta directa, FedEx, DHL
    u otra estrategia futura.
    """

    shipping_order: object
    packing_order: object
    picking_order: object
    warehouse: object
    origin: object
    destination: object | None
    order: object
    integration: object | None
    carrier_shipment: object
    origin_data: dict
    destination_data: dict


# ============================================================
# UTILIDADES DE DIRECCIONES
# ============================================================


def _clean_text(value):
    """
    Convierte cualquier valor a texto limpio.
    """

    return str(value or "").strip()


def _first_text(
    data,
    *keys,
    default="",
):
    """
    Obtiene el primer valor de texto disponible
    dentro de un diccionario.
    """

    if not isinstance(data, dict):
        return _clean_text(default)

    for key in keys:
        value = _clean_text(data.get(key))

        if value:
            return value

    return _clean_text(default)


def _build_complete_street(data):
    """
    Construye una calle completa utilizando:
    calle, número exterior y número interior.
    """

    street = _clean_text(data.get("street"))

    exterior_number = _clean_text(data.get("exterior_number") or data.get("number"))

    interior_number = _clean_text(data.get("interior_number"))

    parts = [
        street,
        exterior_number,
    ]

    if interior_number:
        parts.append(f"Int. {interior_number}")

    return " ".join(value for value in parts if value)


def _build_complete_address(data):
    """
    Construye una dirección completa para mostrar
    en la interfaz de Shipping Hub.
    """

    parts = [
        _clean_text(data.get("complete_street")),
        _clean_text(data.get("neighborhood")),
        _clean_text(data.get("city")),
        _clean_text(data.get("state")),
        _clean_text(data.get("zip_code")),
        _clean_text(data.get("country")),
    ]

    return ", ".join(value for value in parts if value)


def _normalize_address_data(
    data,
    *,
    default_country="México",
    default_country_code="MX",
):
    """
    Convierte cualquier dirección al formato universal
    utilizado por WMS360.

    Mantiene nombres alternativos para que los servicios
    actuales de Envia y las futuras estrategias puedan
    reutilizar la misma información.
    """

    source = dict(data or {})

    label = _first_text(
        source,
        "label",
        "address_label",
    )

    contact_name = _first_text(
        source,
        "contact_name",
        "recipient_name",
        "customer_name",
        "full_name",
        "name",
    )

    company_name = _first_text(
        source,
        "company_name",
        "company",
        "business_name",
    )

    phone = _first_text(
        source,
        "phone",
        "contact_phone",
        "telephone",
        "mobile",
    )

    email = _first_text(
        source,
        "email",
        "contact_email",
    )

    street = _first_text(
        source,
        "street",
        "address_line_1",
    )

    exterior_number = _first_text(
        source,
        "exterior_number",
        "street_number",
        "number",
    )

    interior_number = _first_text(
        source,
        "interior_number",
        "apartment",
        "suite",
    )

    neighborhood = _first_text(
        source,
        "neighborhood",
        "district",
        "colony",
        "suburb",
    )

    city = _first_text(
        source,
        "city",
        "municipality",
        "town",
    )

    state = _first_text(
        source,
        "state",
        "state_name",
    )

    # state_code solo debe contener un código real.
    # No usamos el nombre completo del estado como código.
    state_code = _first_text(
        source,
        "state_code",
    ).upper()

    country = _first_text(
        source,
        "country",
        default=default_country,
    )

    country_code = _first_text(
        source,
        "country_code",
        default=default_country_code,
    ).upper()

    zip_code = _first_text(
        source,
        "zip_code",
        "postal_code",
        "zipcode",
        "postalCode",
    )

    reference = _first_text(
        source,
        "reference",
        "references",
        "address_reference",
    )

    complete_street = _first_text(
        source,
        "complete_street",
    )

    complete_address = _first_text(
        source,
        "complete_address",
    )

    normalized = {
        **source,
        # Identificación
        "address_id": source.get("address_id"),
        "source": _first_text(
            source,
            "source",
        ),
        "label": label,
        # Contacto
        "name": contact_name,
        "contact_name": contact_name,
        "company": company_name,
        "company_name": company_name,
        "phone": phone,
        "email": email,
        # Calle
        "street": street,
        "number": exterior_number,
        "exterior_number": exterior_number,
        "interior_number": interior_number,
        # Colonia y municipio
        "district": neighborhood,
        "neighborhood": neighborhood,
        "city": city,
        "municipality": city,
        # Estado
        "state": state,
        "state_name": state,
        "state_code": state_code,
        # País
        "country": country or default_country,
        "country_code": (country_code or default_country_code),
        # Código postal
        "zip_code": zip_code,
        "postal_code": zip_code,
        "postalCode": zip_code,
        # Referencias
        "reference": reference,
        # Campos completos
        "complete_street": complete_street,
        "complete_address": complete_address,
    }

    if not normalized["complete_street"]:
        normalized["complete_street"] = _build_complete_street(normalized)

    if not normalized["complete_address"]:
        normalized["complete_address"] = _build_complete_address(normalized)

    return normalized


def _validate_address_data(
    data,
    *,
    address_name,
):
    """
    Valida los datos mínimos necesarios para cotizar
    y preparar una guía logística.
    """

    required_fields = {
        "contact_name": ("persona de contacto"),
        "phone": "teléfono",
        "street": "calle",
        "city": "ciudad o municipio",
        "state": "estado",
        "zip_code": "código postal",
        "country_code": "código de país",
    }

    missing_fields = [
        field_label
        for field_name, field_label in required_fields.items()
        if not _clean_text(data.get(field_name))
    ]

    if missing_fields:
        raise ValidationError(
            (
                f"La dirección de {address_name} "
                "está incompleta. Falta: " + ", ".join(missing_fields) + "."
            )
        )

    country_code = _clean_text(data.get("country_code")).upper()

    if len(country_code) != 2:
        raise ValidationError(
            (f"El código de país de {address_name} " "debe tener dos letras.")
        )

    zip_code = _clean_text(data.get("zip_code"))

    if country_code == "MX":
        if not zip_code.isdigit() or len(zip_code) != 5:
            raise ValidationError(
                (
                    f"El código postal de {address_name} "
                    "debe tener exactamente 5 números."
                )
            )


# ============================================================
# ORIGEN
# ============================================================


def build_origin_data(origin):
    """
    Convierte la dirección logística de la bodega
    al formato universal de WMS360.
    """

    if origin is None:
        raise ValidationError("No se recibió una dirección de origen.")

    warehouse = getattr(
        origin,
        "warehouse",
        None,
    )

    warehouse_name = _clean_text(
        getattr(
            warehouse,
            "name",
            "",
        )
    )

    company = getattr(
        origin,
        "company",
        None,
    )

    company_name = _clean_text(
        getattr(
            origin,
            "company_name",
            "",
        )
        or getattr(
            company,
            "name",
            "",
        )
    )

    data = {
        "address_id": origin.id,
        "source": "warehouse_origin",
        "label": (warehouse_name or company_name or "Bodega de origen"),
        "contact_name": (origin.contact_name),
        "company_name": company_name,
        "phone": origin.phone,
        "email": (origin.email or ""),
        "street": origin.street,
        "exterior_number": (origin.exterior_number or ""),
        "interior_number": (origin.interior_number or ""),
        "neighborhood": (origin.neighborhood or ""),
        "city": origin.city,
        "state": origin.state,
        "state_code": "",
        "country": (origin.country or "México"),
        "country_code": (origin.country_code or "MX"),
        "zip_code": origin.zip_code,
        "reference": (origin.reference or ""),
        "complete_street": _clean_text(
            getattr(
                origin,
                "complete_street",
                "",
            )
        ),
    }

    origin_data = _normalize_address_data(data)

    _validate_address_data(
        origin_data,
        address_name="origen",
    )

    return origin_data


def _build_origin_data(origin):
    """
    Alias interno conservado por compatibilidad.
    """

    return build_origin_data(origin)


# ============================================================
# DESTINO
# ============================================================


def _build_destination_data_from_snapshot(
    snapshot,
):
    """
    Normaliza directamente un snapshot de entrega.
    """

    if not isinstance(snapshot, dict):
        raise ValidationError(
            ("La dirección de entrega guardada " "no tiene un formato válido.")
        )

    source = dict(snapshot)

    # También soporta snapshots anidados.
    for nested_key in (
        "address",
        "shipping_address",
        "destination",
    ):
        nested_data = source.get(nested_key)

        if isinstance(nested_data, dict):
            source = {
                **source,
                **nested_data,
            }
            break

    destination_data = _normalize_address_data(source)

    if not destination_data.get("label"):
        destination_data["label"] = "Dirección de entrega"

    _validate_address_data(
        destination_data,
        address_name="destino",
    )

    return destination_data


def build_destination_data(order):
    """
    Recupera exclusivamente la fotografía de entrega
    congelada cuando se creó el pedido.

    No reconstruye el destino utilizando datos actuales
    del CRM.
    """

    if order is None:
        raise ValidationError("No se recibió un pedido válido.")

    snapshot = dict(order.shipping_address_snapshot or {})

    if not snapshot:
        raise ValidationError(
            ("El pedido no tiene una dirección " "de entrega congelada.")
        )

    snapshot.setdefault(
        "source",
        getattr(
            order,
            "shipping_address_source",
            "",
        )
        or "",
    )

    snapshot.setdefault(
        "address_id",
        getattr(
            order,
            "shipping_address_id",
            None,
        ),
    )

    return _build_destination_data_from_snapshot(snapshot)


# ============================================================
# PREPARAR MESA DE CUBICACIÓN
# ============================================================


def prepare_cubication(
    *,
    company,
    shipping_id,
    user=None,
    require_integration=False,
    default_source=CarrierShipment.SOURCE_AUTOMATIC,
):
    """
    Prepara toda la información necesaria para mostrar
    y operar la Mesa de Cubicación.

    La cubicación operativa no depende de un proveedor. Solo cotizar o
    generar una guía automática exige una integración conectada.

    La autorización de usuario/bodega se realiza en la capa HTTP, una vez
    resuelta la bodega real del envío. Este servicio conserva el aislamiento
    por empresa incluso cuando se usa desde tareas internas.
    """

    if company is None:
        raise ValidationError("No se detectó la empresa actual.")

    # =========================================================
    # ENVÍO
    # =========================================================

    shipping_order = (
        ShippingOrder.objects.select_related(
            "company",
            "order",
            "order__customer",
            "order__shipping_address",
            "packing_order",
            "packing_order__picking_order",
            ("packing_order__" "picking_order__warehouse"),
        )
        .filter(
            id=shipping_id,
            company=company,
        )
        .first()
    )

    if shipping_order is None:
        raise ValidationError(
            ("El envío no existe o no pertenece " "a la empresa actual.")
        )

    if shipping_order.company_id != company.id:
        raise ValidationError(("El envío no pertenece " "a la empresa actual."))

    if shipping_order.status in {
        ShippingOrder.STATUS_SHIPPED,
        ShippingOrder.STATUS_DELIVERED,
        ShippingOrder.STATUS_CANCELLED,
    }:
        raise ValidationError(
            ("Este envío ya no puede modificarse " "desde la Mesa de Cubicación.")
        )

    # =========================================================
    # PACKING, PICKING Y BODEGA
    # =========================================================

    packing_order = shipping_order.packing_order

    if packing_order is None:
        raise ValidationError(
            ("El envío no tiene una orden " "de empaque relacionada.")
        )

    picking_order = packing_order.picking_order

    if picking_order is None:
        raise ValidationError(
            ("La orden de empaque no tiene " "un picking relacionado.")
        )

    warehouse = picking_order.warehouse

    if warehouse is None:
        raise ValidationError(("No se encontró la bodega " "relacionada con el envío."))

    if warehouse.company_id != company.id:
        raise ValidationError(
            ("La bodega del envío no pertenece " "a la empresa actual.")
        )

    # =========================================================
    # ORIGEN LOGÍSTICO
    # =========================================================

    origin = (
        ShippingOriginAddress.objects.for_company(company)
        .select_related(
            "warehouse",
            "company",
        )
        .filter(
            warehouse=warehouse,
            is_active=True,
        )
        .order_by(
            "-updated_at",
            "-id",
        )
        .first()
    )

    if origin is None:
        raise ValidationError(
            ("La bodega no tiene una dirección " "de origen activa en Shipping Hub.")
        )

    if origin.company_id != company.id:
        raise ValidationError(
            ("La dirección de origen no pertenece " "a la empresa actual.")
        )

    # =========================================================
    # PEDIDO Y DESTINO
    # =========================================================

    order = shipping_order.order

    if order is None:
        raise ValidationError(("El envío no tiene un pedido " "relacionado."))

    if order.company_id != company.id:
        raise ValidationError(("El pedido no pertenece " "a la empresa actual."))

    if not order.shipping_address_snapshot:
        raise ValidationError(
            ("El pedido no tiene una dirección " "de entrega guardada.")
        )

    destination = order.shipping_address

    if destination is not None and destination.company_id != company.id:
        raise ValidationError(
            ("La dirección de entrega no pertenece " "a la empresa actual.")
        )

    # =========================================================
    # DATOS NORMALIZADOS
    # =========================================================

    origin_data = build_origin_data(origin)

    destination_data = build_destination_data(order)

    # =========================================================
    # INTEGRACIÓN LOGÍSTICA PRINCIPAL
    # =========================================================

    # La integración se intenta resolver siempre para que la Mesa de Cubicación
    # reconozca una cuenta activa (por ejemplo, Skydropx) aunque abrir la mesa no
    # exija tener proveedor. ``require_integration`` controla únicamente si la
    # ausencia de integración debe bloquear la operación.
    try:
        integration = get_primary_shipping_integration(company=company)
    except ValidationError:
        if require_integration:
            raise
        integration = None

    if integration is not None:
        if integration.company_id != company.id:
            raise ValidationError(
                "La integración logística principal no pertenece a la empresa actual."
            )
        if not integration.is_active or not integration.is_primary:
            raise ValidationError(
                "La integración logística principal no está activa y seleccionada."
            )
        if not integration.last_connection_test_success:
            if require_integration:
                raise ValidationError(
                    "La integración logística principal no tiene una prueba de conexión "
                    "exitosa. Prueba nuevamente la conexión desde Shipping Hub."
                )
            integration = None

    # =========================================================
    # BORRADOR DE GUÍA
    # =========================================================

    carrier_shipment, created = CarrierShipment.objects.for_company(
        company
    ).get_or_create(
        shipping_order=shipping_order,
        defaults={
            "company": company,
            "provider": (
                integration.provider
                if integration is not None
                else {
                    CarrierShipment.SOURCE_EXTERNAL: "manual",
                    CarrierShipment.SOURCE_LOCAL: "local",
                    CarrierShipment.SOURCE_CUSTOM: "custom",
                    CarrierShipment.SOURCE_DIRECT: "direct",
                }.get(default_source, "manual")
            ),
            "source": default_source,
            "status": (CarrierShipment.STATUS_DRAFT),
        },
    )

    if carrier_shipment.company_id != company.id:
        raise ValidationError(
            ("El borrador de la guía no pertenece " "a la empresa actual.")
        )

    if (
        integration is not None
        and not created
        and carrier_shipment.provider != integration.provider
    ):
        if carrier_shipment.status != CarrierShipment.STATUS_DRAFT:
            raise ValidationError(
                ("El envío ya está siendo procesado " "con otro proveedor logístico.")
            )

        carrier_shipment.provider = integration.provider

        carrier_shipment.save(
            update_fields=[
                "provider",
                "updated_at",
            ]
        )

    # =========================================================
    # RETORNAR CONTEXTO
    # =========================================================

    return CubicationContext(
        shipping_order=shipping_order,
        packing_order=packing_order,
        picking_order=picking_order,
        warehouse=warehouse,
        origin=origin,
        destination=destination,
        order=order,
        integration=integration,
        carrier_shipment=carrier_shipment,
        origin_data=origin_data,
        destination_data=destination_data,
    )
