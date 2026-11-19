from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError

from shipping_hub.models import CompanyShippingIntegration

from .credentials import (
    CredentialConfigurationError,
    CredentialDecryptionError,
    CredentialValidationError,
    decrypt_credentials,
    is_wms360_encrypted_value,
)

# ============================================================
# EXCEPCIONES
# ============================================================


class IntegrationCredentialError(ValidationError):
    """
    Error explícito al resolver credenciales
    de una integración logística.
    """

    pass


# ============================================================
# CAMPOS REQUERIDOS POR PROVEEDOR
# ============================================================

# Por ahora solamente exigimos campos exactos para proveedores
# cuya autenticación ya está implementada en WMS360.
#
# Los proveedores directos tendrán sus propios campos cuando
# implementemos sus estrategias reales.

PROVIDER_REQUIRED_CREDENTIAL_FIELDS = {
    CompanyShippingIntegration.PROVIDER_ENVIA: ("api_token",),
    CompanyShippingIntegration.PROVIDER_SKYDROPX: ("client_id", "client_secret"),
    CompanyShippingIntegration.PROVIDER_ESTAFETA_DIRECT: (
        "api_key", "client_id", "client_secret", "customer_number",
    ),
    CompanyShippingIntegration.PROVIDER_FEDEX_DIRECT: (
        "client_id", "client_secret", "account_number",
    ),
    CompanyShippingIntegration.PROVIDER_DHL_DIRECT: (
        "api_key", "api_secret", "account_number",
    ),
}


# ============================================================
# UTILIDADES
# ============================================================


def _clean_text(
    value: Any,
) -> str:
    """
    Convierte un valor sencillo a texto limpio.
    """

    return str(value or "").strip()


def _value_is_configured(
    value: Any,
) -> bool:
    """
    Comprueba recursivamente si existe al menos
    un valor real configurado.
    """

    if isinstance(value, Mapping):
        return any(_value_is_configured(item) for item in value.values())

    if isinstance(
        value,
        (list, tuple, set),
    ):
        return any(_value_is_configured(item) for item in value)

    if value is None:
        return False

    if isinstance(value, str):
        return bool(value.strip())

    return True


def _credentials_have_values(
    credentials: Mapping[str, Any],
) -> bool:
    """
    Comprueba que el contenedor tenga al menos
    una credencial configurada.
    """

    return any(_value_is_configured(value) for value in credentials.values())


def _normalize_mapping(
    credentials: Any,
) -> dict[str, Any]:
    """
    Convierte las credenciales a un diccionario
    normalizado y comprueba que tenga contenido.
    """

    if not isinstance(
        credentials,
        Mapping,
    ):
        raise IntegrationCredentialError(
            ("Las credenciales de la integración " "no tienen un formato válido.")
        )

    normalized: dict[str, Any] = {}

    for raw_key, value in credentials.items():
        key = _clean_text(raw_key)

        if not key:
            raise IntegrationCredentialError(
                ("Las credenciales contienen " "un nombre de campo vacío.")
            )

        normalized[key] = value

    if not normalized:
        raise IntegrationCredentialError(
            ("La integración no contiene " "credenciales configuradas.")
        )

    if not _credentials_have_values(normalized):
        raise IntegrationCredentialError(
            ("Las credenciales de la integración " "no contienen valores configurados.")
        )

    return normalized


def _validate_provider_credentials(
    *,
    provider: str,
    credentials: Any,
) -> dict[str, Any]:
    """
    Valida los campos obligatorios del proveedor.

    No realiza fallback ni busca aliases antiguos.
    """

    normalized = _normalize_mapping(credentials)

    required_fields = PROVIDER_REQUIRED_CREDENTIAL_FIELDS.get(
        provider,
        (),
    )

    missing_fields = [
        field_name
        for field_name in required_fields
        if not _clean_text(normalized.get(field_name))
    ]

    if missing_fields:
        raise IntegrationCredentialError(
            (
                "Las credenciales del proveedor "
                "están incompletas. Falta: " + ", ".join(missing_fields) + "."
            )
        )

    return normalized


def _validate_integration_company(
    *,
    integration: CompanyShippingIntegration,
    company=None,
) -> None:
    """
    Impide usar una integración perteneciente
    a otra empresa.
    """

    if company is None:
        return

    company_id = getattr(
        company,
        "id",
        None,
    )

    if not company_id:
        raise IntegrationCredentialError(
            ("No fue posible identificar " "la empresa actual.")
        )

    if integration.company_id != company_id:
        raise IntegrationCredentialError(
            ("La integración logística no " "pertenece a la empresa actual.")
        )


# ============================================================
# CUENTA MAESTRA DE WMS360
# ============================================================


def _get_master_credentials(
    *,
    integration: CompanyShippingIntegration,
) -> dict[str, Any]:
    """
    Obtiene credenciales maestras exclusivamente
    desde settings y variables de entorno.

    Nunca consulta credentials_encrypted.
    """

    master_credentials = getattr(
        settings,
        "SHIPPING_HUB_MASTER_CREDENTIALS",
        {},
    )

    if not isinstance(
        master_credentials,
        Mapping,
    ):
        raise IntegrationCredentialError(
            ("SHIPPING_HUB_MASTER_CREDENTIALS " "no tiene una configuración válida.")
        )

    provider_config = master_credentials.get(integration.provider)

    if not isinstance(
        provider_config,
        Mapping,
    ):
        raise IntegrationCredentialError(
            (
                "WMS360 no tiene una cuenta maestra "
                "configurada para el proveedor "
                f"{integration.get_provider_display()}."
            )
        )

    environment_config = provider_config.get(integration.environment)

    if not isinstance(
        environment_config,
        Mapping,
    ):
        raise IntegrationCredentialError(
            (
                "WMS360 no tiene credenciales maestras "
                "para el proveedor "
                f"{integration.get_provider_display()} "
                "en el entorno "
                f"{integration.get_environment_display()}."
            )
        )

    return _validate_provider_credentials(
        provider=integration.provider,
        credentials=environment_config,
    )


# ============================================================
# CUENTA PROPIA DE LA EMPRESA
# ============================================================


def _get_company_credentials(
    *,
    integration: CompanyShippingIntegration,
) -> dict[str, Any]:
    """
    Obtiene exclusivamente las credenciales cifradas
    pertenecientes a la empresa de la integración.

    Solo admite el formato seguro oficial:
    wms360-fernet-v1.
    """

    encrypted_value = _clean_text(integration.credentials_encrypted)

    if not encrypted_value:
        raise IntegrationCredentialError(
            (
                "La empresa seleccionó una cuenta "
                "propia, pero no tiene credenciales "
                "configuradas."
            )
        )

    if not is_wms360_encrypted_value(encrypted_value):
        raise IntegrationCredentialError(
            (
                "Las credenciales de esta integración "
                "utilizan un formato antiguo no admitido. "
                "Vuelve a guardar las credenciales desde "
                "la configuración logística."
            )
        )

    try:
        credentials = decrypt_credentials(encrypted_value)

    except (
        CredentialConfigurationError,
        CredentialDecryptionError,
        CredentialValidationError,
    ) as error:
        raise IntegrationCredentialError(
            ("No fue posible descifrar las " "credenciales seguras de la empresa.")
        ) from error

    return _validate_provider_credentials(
        provider=integration.provider,
        credentials=credentials,
    )


# ============================================================
# RESOLUCIÓN ESTRICTA
# ============================================================


def resolve_integration_credentials(
    *,
    integration: CompanyShippingIntegration,
    company=None,
) -> dict[str, Any]:
    """
    Resuelve credenciales según account_mode.

    Reglas obligatorias:

    master:
        Solo credenciales desde settings/.env.

    company:
        Solo credenciales cifradas de la empresa.

    No existe fallback entre ambos modos.
    """

    if not isinstance(
        integration,
        CompanyShippingIntegration,
    ):
        raise IntegrationCredentialError(("La integración logística " "no es válida."))

    _validate_integration_company(
        integration=integration,
        company=company,
    )

    if integration.account_mode == CompanyShippingIntegration.ACCOUNT_MODE_MASTER:
        if _clean_text(integration.credentials_encrypted):
            raise IntegrationCredentialError(
                (
                    "La integración está configurada "
                    "como cuenta maestra, pero conserva "
                    "credenciales propias de la empresa. "
                    "Guarda nuevamente la integración "
                    "para limpiar ese contenido."
                )
            )

        return _get_master_credentials(
            integration=integration,
        )

    if integration.account_mode == CompanyShippingIntegration.ACCOUNT_MODE_COMPANY:
        return _get_company_credentials(
            integration=integration,
        )

    raise IntegrationCredentialError(
        ("La integración logística no tiene " "un modo de cuenta válido.")
    )


# ============================================================
# ACCESO A CREDENCIALES ESPECÍFICAS
# ============================================================


def get_integration_credential(
    *,
    integration: CompanyShippingIntegration,
    credential_name: str,
    company=None,
) -> Any:
    """
    Obtiene una credencial concreta de manera estricta.
    """

    clean_name = _clean_text(credential_name)

    if not clean_name:
        raise IntegrationCredentialError(
            ("No se indicó el nombre " "de la credencial requerida.")
        )

    credentials = resolve_integration_credentials(
        integration=integration,
        company=company,
    )

    value = credentials.get(clean_name)

    if not _value_is_configured(value):
        raise IntegrationCredentialError(
            ("Las credenciales del proveedor " f"no contienen {clean_name}.")
        )

    return value


def get_integration_api_token(
    *,
    integration: CompanyShippingIntegration,
    company=None,
) -> str:
    """
    Obtiene el api_token oficial.

    No busca token, access_token, envia_token
    ni ningún alias del sistema antiguo.
    """

    api_token = get_integration_credential(
        integration=integration,
        credential_name="api_token",
        company=company,
    )

    clean_token = _clean_text(api_token)

    if not clean_token:
        raise IntegrationCredentialError(
            ("Las credenciales del proveedor " "no contienen api_token.")
        )

    return clean_token
