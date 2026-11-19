from __future__ import annotations

import hashlib
import secrets

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from shipping_hub.models import (
    CompanyShippingIntegration,
    IntegrationSecret,
    IntegrationSecretAuditEvent,
    IntegrationSecretVersion,
)
from shipping_hub.services.credentials import (
    decrypt_credentials,
    encrypt_credentials,
)


class SecretManagerError(Exception):
    pass


def generate_secret(*, bytes_length: int = 48) -> str:
    bytes_length = max(32, min(int(bytes_length), 128))
    return secrets.token_urlsafe(bytes_length)


def secret_fingerprint(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def masked_hint(value: str) -> str:
    text = str(value or "")
    if len(text) <= 4:
        return "****"
    return f"****{text[-4:]}"


def _audit(*, secret, version, action, actor=None, reason="", metadata=None):
    IntegrationSecretAuditEvent.objects.create(
        company_id=secret.company_id,
        secret=secret,
        version=version,
        action=action,
        actor=actor,
        reason=str(reason or "")[:500],
        metadata=metadata or {},
    )


def rotate_integration_secret(
    *,
    integration: CompanyShippingIntegration,
    name: str,
    kind: str,
    value: str,
    actor=None,
    reason: str = "",
    purpose: str = "",
    sync_legacy_webhook: bool = False,
):
    value = str(value or "").strip()
    if not value:
        raise SecretManagerError("El secreto no puede estar vacío.")
    if integration is None or not integration.pk:
        raise SecretManagerError("Se requiere una integración guardada.")

    with transaction.atomic():
        locked_integration = (
            CompanyShippingIntegration.objects.select_for_update()
            .select_related("company")
            .get(pk=integration.pk, company_id=integration.company_id)
        )

        secret, created = IntegrationSecret.objects.select_for_update().get_or_create(
            company_id=locked_integration.company_id,
            integration=locked_integration,
            name=name,
            defaults={
                "kind": kind,
                "purpose": purpose,
                "created_by": actor,
                "is_enabled": True,
            },
        )

        if secret.kind != kind:
            raise SecretManagerError(
                "Ya existe un secreto con ese nombre y otro tipo."
            )

        current = (
            IntegrationSecretVersion.objects.select_for_update()
            .filter(
                company_id=secret.company_id,
                secret=secret,
                status=IntegrationSecretVersion.STATUS_ACTIVE,
            )
            .first()
        )

        fingerprint = secret_fingerprint(value)
        if current and current.fingerprint_sha256 == fingerprint:
            return secret, current, False

        if current:
            current.status = IntegrationSecretVersion.STATUS_SUPERSEDED
            current.revoked_at = timezone.now()
            current.revoked_by = actor
            current.save(
                update_fields=[
                    "status",
                    "revoked_at",
                    "revoked_by",
                ]
            )

        latest_version = (
            IntegrationSecretVersion.objects.filter(secret=secret)
            .aggregate(max_version=Max("version"))
            .get("max_version")
            or 0
        )

        encrypted_value = encrypt_credentials(
            {
                "secret_value": value,
                "secret_name": name,
                "secret_kind": kind,
            }
        )
        version = IntegrationSecretVersion.objects.create(
            company_id=secret.company_id,
            secret=secret,
            version=latest_version + 1,
            encrypted_value=encrypted_value,
            fingerprint_sha256=fingerprint,
            masked_hint=masked_hint(value),
            status=IntegrationSecretVersion.STATUS_ACTIVE,
            rotation_reason=str(reason or "")[:500],
            created_by=actor,
        )

        if sync_legacy_webhook:
            locked_integration.webhook_secret_encrypted = encrypt_credentials(
                {"webhook_secret": value}
            )
            locked_integration.save(
                update_fields=["webhook_secret_encrypted", "updated_at"]
            )
            _audit(
                secret=secret,
                version=version,
                action=IntegrationSecretAuditEvent.ACTION_LEGACY_SYNCED,
                actor=actor,
                reason="Compatibilidad temporal con el lector heredado.",
                metadata={"field": "webhook_secret_encrypted"},
            )

        _audit(
            secret=secret,
            version=version,
            action=(
                IntegrationSecretAuditEvent.ACTION_CREATED
                if created
                else IntegrationSecretAuditEvent.ACTION_ROTATED
            ),
            actor=actor,
            reason=reason,
            metadata={
                "kind": kind,
                "version": version.version,
                "masked_hint": version.masked_hint,
            },
        )

    return secret, version, True


def get_active_secret_value(
    *,
    integration: CompanyShippingIntegration,
    name: str,
    audit_read: bool = False,
    actor=None,
) -> str:
    version = (
        IntegrationSecretVersion.objects
        .select_related("secret", "secret__integration")
        .get(
            company_id=integration.company_id,
            secret__integration_id=integration.pk,
            secret__name=name,
            secret__is_enabled=True,
            status=IntegrationSecretVersion.STATUS_ACTIVE,
        )
    )
    payload = decrypt_credentials(version.encrypted_value)
    value = str(payload.get("secret_value") or "")
    if not value:
        raise SecretManagerError("La versión activa no contiene un valor válido.")

    if audit_read:
        _audit(
            secret=version.secret,
            version=version,
            action=IntegrationSecretAuditEvent.ACTION_READ,
            actor=actor,
            metadata={"masked_hint": version.masked_hint},
        )
    return value


def revoke_active_secret(
    *,
    integration: CompanyShippingIntegration,
    name: str,
    actor=None,
    reason: str = "",
):
    with transaction.atomic():
        version = (
            IntegrationSecretVersion.objects.select_for_update()
            .select_related("secret")
            .get(
                company_id=integration.company_id,
                secret__integration_id=integration.pk,
                secret__name=name,
                status=IntegrationSecretVersion.STATUS_ACTIVE,
            )
        )
        version.status = IntegrationSecretVersion.STATUS_REVOKED
        version.revoked_at = timezone.now()
        version.revoked_by = actor
        version.save(
            update_fields=["status", "revoked_at", "revoked_by"]
        )
        _audit(
            secret=version.secret,
            version=version,
            action=IntegrationSecretAuditEvent.ACTION_REVOKED,
            actor=actor,
            reason=reason,
        )
    return version
