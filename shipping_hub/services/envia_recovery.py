from __future__ import annotations

from decimal import Decimal, InvalidOperation
from io import BytesIO
import zipfile

from django.core.files.base import ContentFile
from django.db import transaction

from shipping_hub.models import ShipmentPackage
from shipping_hub.services.envia import EnviaAPIError, EnviaClient
from shipping_hub.services.integration_credentials import (
    IntegrationCredentialError,
    get_integration_api_token,
)
from shipping_hub.services.lifecycle_service import (
    SOURCE_RECOVERY_ENGINE,
    transition_package,
    transition_shipment,
)
from shipping_hub.strategies.contracts import ProviderLabel, ProviderPurchaseResult


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _unwrap(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return payload
    for key in ("data", "shipment", "guide", "result"):
        value = payload.get(key)
        if isinstance(value, (dict, list)):
            return value
    return payload


def _rows(payload):
    payload = _unwrap(payload)
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("shipments", "guides", "results", "items", "rows", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return [payload]


def _value(data, *keys):
    if not isinstance(data, dict):
        return ""
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return ""


def _tracking(data):
    return str(
        _value(
            data,
            "tracking_number",
            "trackingNumber",
            "tracking",
            "guide",
            "guide_number",
            "guideNumber",
        )
        or ""
    ).strip()


def _shipment_id(data):
    return str(
        _value(
            data,
            "shipment_id",
            "shipmentId",
            "id",
            "guide_id",
            "guideId",
        )
        or ""
    ).strip()


def _carrier(data):
    value = _value(data, "carrier", "carrier_name", "carrierName")
    if isinstance(value, dict):
        return str(
            value.get("name")
            or value.get("description")
            or value.get("code")
            or ""
        ).strip()
    return str(value or "").strip()


def _created_at(data):
    return str(
        _value(data, "created_at", "createdAt", "created", "date")
        or ""
    ).strip()


def _find_candidate(rows, *, shipment, purchase):
    expected_id = str(
        purchase.provider_purchase_id
        or shipment.external_shipment_id
        or ""
    ).strip()
    expected_tracking = str(shipment.master_tracking_number or "").strip()
    expected_carrier = str(
        shipment.carrier_code or shipment.carrier_name or ""
    ).casefold()

    if expected_id:
        for row in rows:
            if _shipment_id(row) == expected_id:
                return row

    if expected_tracking:
        for row in rows:
            if _tracking(row) == expected_tracking:
                return row

    if expected_carrier:
        candidates = [
            row
            for row in rows
            if (
                expected_carrier in _carrier(row).casefold()
                or _carrier(row).casefold() in expected_carrier
            )
        ]
        if len(candidates) == 1:
            return candidates[0]

    return None


def _first_pdf_from_zip(content):
    with zipfile.ZipFile(BytesIO(content)) as archive:
        names = [
            name
            for name in archive.namelist()
            if not name.endswith("/") and name.lower().endswith(".pdf")
        ]
        if not names:
            return None
        return archive.read(names[0])


def _save_label_file(*, package, content, extension, tracking):
    if extension == "zip":
        pdf = _first_pdf_from_zip(content)
        if not pdf:
            raise EnviaAPIError(
                "El ZIP de Envia no contiene una etiqueta PDF.",
                error_code="zip_without_pdf",
            )
        content = pdf
        extension = "pdf"

    if extension != "pdf" or not content.startswith(b"%PDF"):
        raise EnviaAPIError(
            "La etiqueta recuperada de Envia no es un PDF válido.",
            error_code="invalid_pdf",
        )

    safe_tracking = "".join(
        char
        for char in str(tracking or package.pk)
        if char.isalnum() or char in ("-", "_")
    )
    filename = f"envia_{safe_tracking or package.pk}.pdf"
    package.label_file.save(filename, ContentFile(content), save=False)
    package.label_format = ShipmentPackage.LABEL_FORMAT_PDF
    package.external_label_original_name = filename
    package.external_label_content_type = "application/pdf"


def recover_envia_purchase(*, purchase) -> ProviderPurchaseResult:
    shipment = purchase.carrier_shipment
    integration = purchase.integration

    if shipment.company_id != purchase.company_id:
        return ProviderPurchaseResult(
            outcome="failed_final",
            retry_disposition="manual_review",
            error_code="tenant_mismatch",
            error_message="La compra y el envío pertenecen a empresas diferentes.",
        )

    try:
        token = get_integration_api_token(
            integration=integration,
            company=purchase.company,
        )
    except IntegrationCredentialError as error:
        return ProviderPurchaseResult(
            outcome="failed_final",
            retry_disposition="manual_review",
            error_code="credential_error",
            error_message=str(error),
        )

    client = EnviaClient(
        api_token=token,
        environment=integration.environment,
        timeout=30,
    )

    remote_id = str(
        purchase.provider_purchase_id
        or shipment.external_shipment_id
        or ""
    ).strip()
    tracking = str(shipment.master_tracking_number or "").strip()
    candidate = None
    raw_lookup = {}

    try:
        if remote_id:
            raw_lookup = client.get_shipment_by_id(remote_id)
        elif tracking:
            raw_lookup = client.get_shipment_by_tracking(tracking)
        else:
            raw_lookup = client.list_shipments_by_month(
                month=purchase.requested_at.month,
                year=purchase.requested_at.year,
            )

        candidate = _find_candidate(
            _rows(raw_lookup),
            shipment=shipment,
            purchase=purchase,
        )
    except EnviaAPIError as error:
        if error.error_code == "resource_not_found":
            return ProviderPurchaseResult(
                outcome="pending",
                retry_disposition="reconcile_only",
                provider_purchase_id=remote_id,
                error_code="envia_shipment_not_found_yet",
                error_message="Envia todavía no devuelve una coincidencia para la compra.",
            )
        return ProviderPurchaseResult(
            outcome="uncertain",
            retry_disposition="reconcile_only",
            provider_purchase_id=remote_id,
            error_code=error.error_code or "envia_lookup_error",
            error_message=str(error),
            raw_response={"status_code": error.status_code},
        )

    if candidate is None:
        return ProviderPurchaseResult(
            outcome="pending",
            retry_disposition="reconcile_only",
            provider_purchase_id=remote_id,
            error_code="envia_no_safe_match",
            error_message=(
                "Envia respondió, pero WMS360 no encontró una coincidencia "
                "suficientemente segura. La recompra permanece bloqueada."
            ),
        )

    found_id = _shipment_id(candidate) or remote_id
    found_tracking = _tracking(candidate) or tracking
    if not found_id:
        return ProviderPurchaseResult(
            outcome="uncertain",
            retry_disposition="reconcile_only",
            tracking_numbers=((found_tracking,) if found_tracking else ()),
            error_code="envia_missing_shipment_id",
            error_message=(
                "La guía aparece en Envia, pero falta el identificador requerido "
                "para descargar la etiqueta."
            ),
        )

    try:
        label_download = client.download_existing_labels([found_id])
    except EnviaAPIError as error:
        return ProviderPurchaseResult(
            outcome="pending" if error.is_temporary else "uncertain",
            retry_disposition="reconcile_only",
            provider_purchase_id=found_id,
            tracking_numbers=((found_tracking,) if found_tracking else ()),
            error_code=error.error_code or "envia_label_not_ready",
            error_message=str(error),
        )

    with transaction.atomic():
        locked_shipment = type(shipment).objects.select_for_update().get(
            pk=shipment.pk,
            company_id=purchase.company_id,
        )
        packages = list(
            ShipmentPackage.objects.select_for_update()
            .filter(
                company_id=purchase.company_id,
                carrier_shipment_id=locked_shipment.pk,
            )
            .order_by("package_number", "pk")
        )
        if not packages:
            return ProviderPurchaseResult(
                outcome="failed_final",
                retry_disposition="manual_review",
                provider_purchase_id=found_id,
                error_code="shipment_without_packages",
                error_message="El envío no tiene bultos para asociar la etiqueta.",
            )

        package = packages[0]
        _save_label_file(
            package=package,
            content=label_download["content"],
            extension=label_download["extension"],
            tracking=found_tracking,
        )
        package.external_package_id = found_id
        package.tracking_number = found_tracking
        transition_package(
            entity_id=package.pk,
            company_id=locked_shipment.company_id,
            to_state=ShipmentPackage.STATUS_LABEL_READY,
            source=SOURCE_RECOVERY_ENGINE,
            reason="Etiqueta recuperada correctamente desde Envia.",
            actor=purchase.requested_by,
            metadata={
                "operation": "recover_envia_purchase",
                "purchase_id": purchase.pk,
                "tracking": found_tracking,
            },
        )
        package.last_error_message = ""
        package.save(
            update_fields=[
                "external_package_id",
                "tracking_number",
                "label_format",
                "label_url",
                "label_file",
                "last_error_message",
                "updated_at",
            ]
        )

        locked_shipment.external_shipment_id = found_id
        locked_shipment.master_tracking_number = found_tracking
        transition_shipment(
            entity_id=locked_shipment.pk,
            company_id=locked_shipment.company_id,
            to_state=locked_shipment.STATUS_LABEL_READY,
            source=SOURCE_RECOVERY_ENGINE,
            reason="Guía recuperada correctamente desde Envia.",
            actor=purchase.requested_by,
            metadata={
                "operation": "recover_envia_purchase",
                "purchase_id": purchase.pk,
                "tracking": found_tracking,
            },
        )
        locked_shipment.last_error_type = ""
        locked_shipment.last_error_code = ""
        locked_shipment.last_error_message = ""
        locked_shipment.save(
            update_fields=[
                "external_shipment_id",
                "master_tracking_number",
                "last_error_type",
                "last_error_code",
                "last_error_message",
                "updated_at",
            ]
        )

    return ProviderPurchaseResult(
        outcome="completed",
        retry_disposition="reconcile_only",
        provider_purchase_id=found_id,
        provider_reference=str(shipment.internal_reference),
        tracking_numbers=((found_tracking,) if found_tracking else ()),
        labels=(
            ProviderLabel(
                tracking_number=found_tracking,
                label_format="pdf",
                external_package_id=found_id,
            ),
        ),
        charged_amount=(
            _decimal(
                _value(candidate, "total_price", "totalPrice", "price", "amount")
            )
            or purchase.charged_amount
            or purchase.quoted_amount
        ),
        currency=str(
            _value(candidate, "currency", "currency_code", "currencyCode")
            or purchase.currency
            or "MXN"
        ).upper(),
        raw_response={
            "source": "envia_queries_recovery",
            "shipment_id": found_id,
            "tracking_number": found_tracking,
            "carrier": _carrier(candidate),
            "created_at": _created_at(candidate),
        },
    )
