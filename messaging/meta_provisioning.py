"""
WMS360 · WhatsApp Meta Embedded Signup · ES-3 HARDENED

Aprovisionamiento idempotente y concurrent-safe de WhatsAppNumber.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

import requests

from django.db import IntegrityError, transaction
from django.utils import timezone

from .meta_embedded_signup import (
    MetaEmbeddedSignupConfigurationError,
    get_embedded_signup_settings,
)
from .models import WhatsAppConfig, WhatsAppNumber


logger = logging.getLogger(__name__)

META_GRAPH_BASE_URL = "https://graph.facebook.com"
META_CONNECT_TIMEOUT_SECONDS = 5
META_READ_TIMEOUT_SECONDS = 20
VERIFYING_LEASE_SECONDS = 120


class MetaProvisioningError(Exception):
    def __init__(self, message, *, status=502, code="meta_provisioning_error"):
        super().__init__(message)
        self.public_message = str(message)
        self.status = int(status)
        self.code = str(code)[:80]


@dataclass(frozen=True)
class MetaProvisioningResult:
    channel_id: int
    status: str
    created: bool
    activated: bool
    display_number: str
    verified_name: str


def _safe_note(message):
    return str(message or "").strip()[:500]


def _subscription_payload_has_app(payload, *, app_id):
    if not isinstance(payload, dict):
        return False

    rows = payload.get("data")
    if not isinstance(rows, list):
        return False

    expected = str(app_id or "").strip()

    for row in rows:
        if not isinstance(row, dict):
            continue

        details = row.get("whatsapp_business_api_data")
        if not isinstance(details, dict):
            continue

        remote_id = str(details.get("id") or "").strip()
        if remote_id and remote_id == expected:
            return True

    return False


def _meta_request(*, method, api_version, object_path, access_token):
    url = (
        f"{META_GRAPH_BASE_URL}/"
        f"{api_version}/"
        f"{str(object_path).lstrip('/')}"
    )

    try:
        response = requests.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=(
                META_CONNECT_TIMEOUT_SECONDS,
                META_READ_TIMEOUT_SECONDS,
            ),
        )
    except requests.Timeout as error:
        raise MetaProvisioningError(
            "Meta no confirmó la operación dentro del tiempo permitido.",
            status=502,
            code="meta_timeout",
        ) from error
    except requests.ConnectionError as error:
        raise MetaProvisioningError(
            "No fue posible establecer conexión con Meta.",
            status=502,
            code="meta_connection_error",
        ) from error
    except requests.RequestException as error:
        raise MetaProvisioningError(
            "Ocurrió un error HTTP comunicándose con Meta.",
            status=502,
            code="meta_http_error",
        ) from error

    try:
        payload = response.json()
    except Exception as error:
        raise MetaProvisioningError(
            "Meta devolvió una respuesta inválida.",
            status=502,
            code="meta_invalid_json",
        ) from error

    if not response.ok:
        raise MetaProvisioningError(
            "Meta rechazó la operación de suscripción del webhook.",
            status=400,
            code="meta_subscription_rejected",
        )

    if not isinstance(payload, dict):
        raise MetaProvisioningError(
            "Meta devolvió una respuesta inesperada.",
            status=502,
            code="meta_invalid_payload",
        )

    return payload


def _is_waba_subscribed(*, meta_configuration, access_token, waba_id):
    payload = _meta_request(
        method="GET",
        api_version=meta_configuration.api_version,
        object_path=f"{waba_id}/subscribed_apps",
        access_token=access_token,
    )

    return _subscription_payload_has_app(
        payload,
        app_id=meta_configuration.app_id,
    )


def _ensure_waba_subscription(*, meta_configuration, access_token, waba_id):
    if _is_waba_subscribed(
        meta_configuration=meta_configuration,
        access_token=access_token,
        waba_id=waba_id,
    ):
        return

    payload = _meta_request(
        method="POST",
        api_version=meta_configuration.api_version,
        object_path=f"{waba_id}/subscribed_apps",
        access_token=access_token,
    )

    success = payload.get("success")

    if not (
        success is True
        or str(success).strip().lower() == "true"
    ):
        raise MetaProvisioningError(
            "Meta respondió sin confirmar la suscripción del webhook.",
            status=502,
            code="meta_subscription_unconfirmed",
        )

    if not _is_waba_subscribed(
        meta_configuration=meta_configuration,
        access_token=access_token,
        waba_id=waba_id,
    ):
        raise MetaProvisioningError(
            "Meta respondió correctamente, pero WMS360 no pudo "
            "confirmar la suscripción del webhook.",
            status=502,
            code="meta_subscription_not_visible",
        )


def _channel_defaults(
    *,
    company,
    warehouse,
    phone_number_id,
    display_number,
    verified_name,
    configuration,
    api_version,
):
    channel_name = (
        str(verified_name or "").strip()
        or f"WhatsApp Meta {str(display_number or '')[-6:]}"
    )[:120]

    return {
        "company": company,
        "warehouse": warehouse,
        "provider": WhatsAppNumber.PROVIDER_META,
        "phone_number_id": phone_number_id,
        "display_number": str(display_number or "").strip()[:30],
        "channel_name": channel_name,
        "meta_access_token": configuration.permanent_access_token,
        "meta_business_account_id": str(configuration.waba_id or "").strip(),
        "meta_api_version": str(api_version or "v23.0").strip()[:20],
        "meta_enabled": False,
        "twilio_enabled": False,
        "ycloud_enabled": False,
        "activation_status": WhatsAppNumber.STATUS_VERIFYING,
        "activation_requested_at": timezone.now(),
        "activated_at": None,
        "is_active": False,
        "activation_notes": (
            "Meta Embedded Signup validado. "
            "Pendiente de confirmar suscripción webhook."
        ),
    }


def _prepare_channel(
    *,
    company,
    warehouse,
    configuration,
    phone_number_id,
    display_number,
    verified_name,
    api_version,
):
    now = timezone.now()
    stale_before = now - timedelta(seconds=VERIFYING_LEASE_SECONDS)

    with transaction.atomic():
        type(company).objects.select_for_update().only("pk").get(pk=company.pk)

        existing = (
            WhatsAppNumber.objects
            .select_for_update()
            .filter(phone_number_id=phone_number_id)
            .first()
        )

        if existing is not None:
            if existing.company_id != company.pk:
                raise MetaProvisioningError(
                    "Este número de WhatsApp ya está asociado "
                    "a otra empresa en WMS360.",
                    status=409,
                    code="phone_owned_by_other_company",
                )

            if existing.warehouse_id and existing.warehouse_id != warehouse.pk:
                raise MetaProvisioningError(
                    "Este número ya está asociado a otra bodega "
                    "de la misma empresa.",
                    status=409,
                    code="phone_owned_by_other_warehouse",
                )

            if existing.provider != WhatsAppNumber.PROVIDER_META:
                raise MetaProvisioningError(
                    "El identificador técnico ya pertenece a "
                    "un canal de otro proveedor.",
                    status=409,
                    code="phone_provider_conflict",
                )

            if existing.activation_status == WhatsAppNumber.STATUS_SUSPENDED:
                raise MetaProvisioningError(
                    "El canal está suspendido y no puede reactivarse "
                    "mediante Embedded Signup.",
                    status=409,
                    code="channel_suspended",
                )

            if (
                existing.activation_status == WhatsAppNumber.STATUS_ACTIVE
                and existing.is_active
                and existing.meta_enabled
            ):
                return existing, False, "already_active", None

            if (
                existing.activation_status == WhatsAppNumber.STATUS_VERIFYING
                and existing.activation_requested_at
                and existing.activation_requested_at > stale_before
            ):
                return existing, False, "in_progress", None

            lease_started_at = timezone.now()

            existing.warehouse = warehouse
            existing.display_number = str(display_number or "").strip()[:30]
            existing.channel_name = (
                str(verified_name or "").strip()
                or existing.channel_name
                or f"WhatsApp Meta {str(display_number or '')[-6:]}"
            )[:120]
            existing.meta_access_token = configuration.permanent_access_token
            existing.meta_business_account_id = str(
                configuration.waba_id or ""
            ).strip()
            existing.meta_api_version = str(
                api_version or "v23.0"
            ).strip()[:20]
            existing.meta_enabled = False
            existing.twilio_enabled = False
            existing.ycloud_enabled = False
            existing.activation_status = WhatsAppNumber.STATUS_VERIFYING
            existing.activation_requested_at = lease_started_at
            existing.activated_at = None
            existing.is_active = False
            existing.activation_notes = (
                "Revalidando suscripción webhook de Meta."
            )

            existing.full_clean()
            existing.save(
                update_fields=[
                    "warehouse",
                    "display_number",
                    "channel_name",
                    "meta_access_token",
                    "meta_business_account_id",
                    "meta_api_version",
                    "meta_enabled",
                    "twilio_enabled",
                    "ycloud_enabled",
                    "activation_status",
                    "activation_requested_at",
                    "activated_at",
                    "is_active",
                    "activation_notes",
                    "updated_at",
                ]
            )

            return existing, False, "leased", lease_started_at

        defaults = _channel_defaults(
            company=company,
            warehouse=warehouse,
            phone_number_id=phone_number_id,
            display_number=display_number,
            verified_name=verified_name,
            configuration=configuration,
            api_version=api_version,
        )
        lease_started_at = defaults["activation_requested_at"]

        channel = WhatsAppNumber(**defaults)
        channel.full_clean()

        try:
            with transaction.atomic():
                channel.save()
        except IntegrityError:
            concurrent = (
                WhatsAppNumber.objects
                .select_for_update()
                .filter(phone_number_id=phone_number_id)
                .first()
            )

            if concurrent is None:
                raise

            if concurrent.company_id != company.pk:
                raise MetaProvisioningError(
                    "Este número fue asociado concurrentemente "
                    "a otra empresa.",
                    status=409,
                    code="phone_concurrent_tenant_conflict",
                )

            return concurrent, False, "in_progress", None

        return channel, True, "leased", lease_started_at


def _release_failed_lease(
    *,
    channel_id,
    company_id,
    lease_started_at,
    reason,
):
    if lease_started_at is None:
        return

    with transaction.atomic():
        channel = (
            WhatsAppNumber.objects
            .select_for_update()
            .filter(
                pk=channel_id,
                company_id=company_id,
            )
            .first()
        )

        if channel is None:
            return

        if channel.activation_status != WhatsAppNumber.STATUS_VERIFYING:
            return

        if channel.activation_requested_at != lease_started_at:
            return

        channel.activation_status = WhatsAppNumber.STATUS_PENDING_ACTIVATION
        channel.meta_enabled = False
        channel.is_active = False
        channel.activation_notes = _safe_note(reason)
        channel.activation_requested_at = None

        channel.full_clean()
        channel.save(
            update_fields=[
                "activation_status",
                "meta_enabled",
                "is_active",
                "activation_notes",
                "activation_requested_at",
                "updated_at",
            ]
        )


def _activate_channel(
    *,
    channel_id,
    company,
    configuration_id,
    lease_started_at,
):
    with transaction.atomic():
        type(company).objects.select_for_update().only("pk").get(pk=company.pk)

        configuration = (
            WhatsAppConfig.objects
            .select_for_update()
            .filter(
                pk=configuration_id,
                company=company,
            )
            .first()
        )

        if configuration is None:
            raise MetaProvisioningError(
                "La configuración Meta dejó de estar disponible.",
                status=409,
                code="configuration_missing",
            )

        channel = (
            WhatsAppNumber.objects
            .select_for_update()
            .filter(
                pk=channel_id,
                company=company,
            )
            .first()
        )

        if channel is None:
            raise MetaProvisioningError(
                "El canal dejó de estar disponible.",
                status=409,
                code="channel_missing",
            )

        if (
            channel.activation_status == WhatsAppNumber.STATUS_ACTIVE
            and channel.is_active
            and channel.meta_enabled
        ):
            return channel

        if (
            channel.activation_status != WhatsAppNumber.STATUS_VERIFYING
            or channel.activation_requested_at != lease_started_at
        ):
            raise MetaProvisioningError(
                "Otro proceso tomó control de la activación. "
                "Recarga el estado del canal.",
                status=409,
                code="lease_lost",
            )

        channel.meta_enabled = True
        channel.is_active = True
        channel.activation_status = WhatsAppNumber.STATUS_ACTIVE
        channel.activated_at = timezone.now()
        channel.activation_notes = (
            "Meta Embedded Signup completado y webhook confirmado."
        )

        channel.full_clean()
        channel.save(
            update_fields=[
                "meta_enabled",
                "is_active",
                "activation_status",
                "activated_at",
                "activation_notes",
                "updated_at",
            ]
        )

        configuration.is_active = True
        configuration.save(
            update_fields=[
                "is_active",
                "updated_at",
            ]
        )

        return channel


def provision_meta_channel(
    *,
    company,
    warehouse,
    configuration,
    phone_number_id,
    display_number,
    verified_name,
):
    if configuration.company_id != company.pk:
        raise MetaProvisioningError(
            "La configuración Meta no pertenece a la empresa actual.",
            status=403,
            code="configuration_tenant_mismatch",
        )

    if warehouse.company_id != company.pk:
        raise MetaProvisioningError(
            "La bodega no pertenece a la empresa actual.",
            status=403,
            code="warehouse_tenant_mismatch",
        )

    if not str(configuration.waba_id or "").strip():
        raise MetaProvisioningError(
            "La configuración Meta no contiene WABA validada.",
            status=409,
            code="missing_waba",
        )

    if not str(configuration.permanent_access_token or "").strip():
        raise MetaProvisioningError(
            "La configuración Meta no contiene credencial validada.",
            status=409,
            code="missing_access_token",
        )

    try:
        meta_configuration = get_embedded_signup_settings()
    except MetaEmbeddedSignupConfigurationError as error:
        raise MetaProvisioningError(
            "Embedded Signup de Meta no está disponible.",
            status=503,
            code="meta_not_configured",
        ) from error

    channel, created, state, lease_started_at = _prepare_channel(
        company=company,
        warehouse=warehouse,
        configuration=configuration,
        phone_number_id=str(phone_number_id).strip(),
        display_number=display_number,
        verified_name=verified_name,
        api_version=meta_configuration.api_version,
    )

    if state == "already_active":
        return MetaProvisioningResult(
            channel_id=channel.pk,
            status="active",
            created=False,
            activated=True,
            display_number=channel.display_number,
            verified_name=channel.channel_name,
        )

    if state == "in_progress":
        return MetaProvisioningResult(
            channel_id=channel.pk,
            status="verifying",
            created=False,
            activated=False,
            display_number=channel.display_number,
            verified_name=channel.channel_name,
        )

    try:
        _ensure_waba_subscription(
            meta_configuration=meta_configuration,
            access_token=configuration.permanent_access_token,
            waba_id=str(configuration.waba_id).strip(),
        )
    except MetaProvisioningError as error:
        _release_failed_lease(
            channel_id=channel.pk,
            company_id=company.pk,
            lease_started_at=lease_started_at,
            reason=(
                "No se confirmó la suscripción webhook de Meta. "
                f"Código interno: {error.code}."
            ),
        )

        logger.warning(
            "Meta ES-3 subscription failed "
            "company_id=%s warehouse_id=%s channel_id=%s code=%s",
            company.pk,
            warehouse.pk,
            channel.pk,
            error.code,
        )
        raise

    channel = _activate_channel(
        channel_id=channel.pk,
        company=company,
        configuration_id=configuration.pk,
        lease_started_at=lease_started_at,
    )

    logger.info(
        "Meta ES-3 channel activated "
        "company_id=%s warehouse_id=%s channel_id=%s",
        company.pk,
        warehouse.pk,
        channel.pk,
    )

    return MetaProvisioningResult(
        channel_id=channel.pk,
        status="active",
        created=created,
        activated=True,
        display_number=channel.display_number,
        verified_name=channel.channel_name,
    )
