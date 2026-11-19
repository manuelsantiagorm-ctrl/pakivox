from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from cryptography.fernet import (
    Fernet,
    InvalidToken,
    MultiFernet,
)
from django.conf import settings
from django.core.exceptions import (
    ImproperlyConfigured,
    ValidationError,
)
from django.utils import timezone

# ============================================================
# CONSTANTES
# ============================================================

CREDENTIAL_PAYLOAD_VERSION = 1

CREDENTIAL_VALUE_PREFIX = "wms360-fernet-v1:"

MASKED_VALUE = "••••••••"


# ============================================================
# EXCEPCIONES
# ============================================================


class CredentialConfigurationError(ImproperlyConfigured):
    """
    Error de configuración del servidor.

    Se utiliza cuando no existe una clave de cifrado válida.
    """

    pass


class CredentialValidationError(ValidationError):
    """
    Error en la estructura de las credenciales.
    """

    pass


class CredentialDecryptionError(ValidationError):
    """
    Error al descifrar credenciales almacenadas.
    """

    pass


# ============================================================
# CONFIGURACIÓN DE CLAVES
# ============================================================


def _get_configured_key_values() -> list[str]:
    """
    Lee las claves de cifrado desde settings.

    La primera clave es la activa para cifrar.
    Las demás se utilizan únicamente para descifrar
    información creada con claves anteriores.
    """

    raw_keys = getattr(
        settings,
        "SHIPPING_HUB_CREDENTIAL_KEYS",
        (),
    )

    if isinstance(raw_keys, str):
        key_values = [key.strip() for key in raw_keys.split(",") if key.strip()]

    elif isinstance(
        raw_keys,
        (list, tuple),
    ):
        key_values = [str(key).strip() for key in raw_keys if str(key).strip()]

    else:
        raise CredentialConfigurationError(
            (
                "SHIPPING_HUB_CREDENTIAL_KEYS "
                "debe ser una lista, tupla o texto "
                "separado por comas."
            )
        )

    if not key_values:
        raise CredentialConfigurationError(
            (
                "No existe una clave para cifrar "
                "las credenciales de Shipping Hub. "
                "Configura "
                "SHIPPING_HUB_CREDENTIAL_KEYS."
            )
        )

    return key_values


def _build_fernet_instances() -> list[Fernet]:
    """
    Valida y construye una instancia Fernet
    por cada clave configurada.
    """

    fernet_instances = []

    for position, key_value in enumerate(
        _get_configured_key_values(),
        start=1,
    ):
        try:
            encoded_key = key_value.encode("ascii")

            fernet_instances.append(Fernet(encoded_key))

        except (
            ValueError,
            TypeError,
            UnicodeEncodeError,
        ) as error:
            raise CredentialConfigurationError(
                (
                    "La clave de cifrado número "
                    f"{position} no tiene un formato "
                    "Fernet válido."
                )
            ) from error

    return fernet_instances


@lru_cache(maxsize=1)
def get_credentials_cipher() -> MultiFernet:
    """
    Retorna el cifrador compartido de Shipping Hub.

    MultiFernet utiliza la primera clave para cifrar
    y prueba todas las claves para descifrar.
    """

    return MultiFernet(_build_fernet_instances())


def clear_credentials_cipher_cache():
    """
    Limpia el cifrador almacenado en memoria.

    Se utiliza principalmente en pruebas o después
    de modificar las claves sin reiniciar el proceso.
    """

    get_credentials_cipher.cache_clear()


# ============================================================
# VALIDACIÓN DE DATOS
# ============================================================


def _validate_credentials_mapping(
    credentials: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Verifica que las credenciales sean un diccionario
    JSON válido y que contengan al menos un valor.
    """

    if not isinstance(credentials, Mapping):
        raise CredentialValidationError(
            ("Las credenciales deben recibirse " "como un diccionario.")
        )

    normalized_credentials = {}

    for raw_key, value in credentials.items():
        if not isinstance(raw_key, str):
            raise CredentialValidationError(
                ("Todos los nombres de credenciales " "deben ser texto.")
            )

        key = raw_key.strip()

        if not key:
            raise CredentialValidationError(
                ("Las credenciales contienen " "un nombre de campo vacío.")
            )

        normalized_credentials[key] = value

    if not normalized_credentials:
        raise CredentialValidationError(
            ("No se recibió ninguna credencial " "para cifrar.")
        )

    has_configured_value = any(
        value
        not in {
            None,
            "",
        }
        for value in normalized_credentials.values()
        if not isinstance(
            value,
            (dict, list),
        )
    )

    has_nested_value = any(
        bool(value)
        for value in normalized_credentials.values()
        if isinstance(
            value,
            (dict, list),
        )
    )

    if not (has_configured_value or has_nested_value):
        raise CredentialValidationError(
            ("Las credenciales no contienen " "ningún valor configurado.")
        )

    try:
        json.dumps(
            normalized_credentials,
            ensure_ascii=False,
            allow_nan=False,
        )

    except (
        TypeError,
        ValueError,
    ) as error:
        raise CredentialValidationError(
            (
                "Las credenciales contienen valores "
                "que no pueden almacenarse como JSON."
            )
        ) from error

    return normalized_credentials


# ============================================================
# CIFRADO
# ============================================================


def encrypt_credentials(
    credentials: Mapping[str, Any],
) -> str:
    """
    Cifra un diccionario de credenciales.

    El resultado está preparado para almacenarse
    directamente en credentials_encrypted.
    """

    normalized_credentials = _validate_credentials_mapping(credentials)

    payload = {
        "version": (CREDENTIAL_PAYLOAD_VERSION),
        "encrypted_at": (timezone.now().isoformat()),
        "credentials": (normalized_credentials),
    }

    serialized_payload = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
        allow_nan=False,
    ).encode("utf-8")

    encrypted_token = (
        get_credentials_cipher().encrypt(serialized_payload).decode("ascii")
    )

    return CREDENTIAL_VALUE_PREFIX + encrypted_token


# ============================================================
# DESCIFRADO
# ============================================================


def decrypt_credentials(
    encrypted_value: str,
) -> dict[str, Any]:
    """
    Descifra el contenido almacenado en
    credentials_encrypted.

    Nunca debe enviarse el resultado completo
    al navegador ni registrarse en logs.
    """

    value = str(encrypted_value or "").strip()

    if not value:
        raise CredentialDecryptionError(
            ("La integración no tiene " "credenciales configuradas.")
        )

    if not value.startswith(CREDENTIAL_VALUE_PREFIX):
        raise CredentialDecryptionError(
            ("Las credenciales almacenadas no " "utilizan el formato seguro esperado.")
        )

    encrypted_token = value[len(CREDENTIAL_VALUE_PREFIX) :]

    if not encrypted_token:
        raise CredentialDecryptionError(
            ("El contenedor de credenciales " "está vacío.")
        )

    try:
        decrypted_payload = get_credentials_cipher().decrypt(
            encrypted_token.encode("ascii")
        )

    except (
        InvalidToken,
        UnicodeEncodeError,
    ) as error:
        raise CredentialDecryptionError(
            (
                "No fue posible descifrar las "
                "credenciales. La clave no coincide "
                "o el contenido fue alterado."
            )
        ) from error

    try:
        payload = json.loads(decrypted_payload.decode("utf-8"))

    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise CredentialDecryptionError(
            ("Las credenciales descifradas " "no contienen JSON válido.")
        ) from error

    if not isinstance(payload, dict):
        raise CredentialDecryptionError(
            ("El contenedor descifrado " "no tiene un formato válido.")
        )

    payload_version = payload.get("version")

    if payload_version != CREDENTIAL_PAYLOAD_VERSION:
        raise CredentialDecryptionError(
            ("La versión del contenedor de " "credenciales no es compatible.")
        )

    credentials = payload.get("credentials")

    try:
        return _validate_credentials_mapping(credentials)

    except CredentialValidationError as error:
        raise CredentialDecryptionError(
            ("Las credenciales descifradas " "están incompletas o dañadas.")
        ) from error


# ============================================================
# ROTACIÓN DE CLAVES
# ============================================================


def rotate_encrypted_credentials(
    encrypted_value: str,
) -> str:
    """
    Vuelve a cifrar un contenedor utilizando
    la primera clave configurada.

    Las claves anteriores deben permanecer temporalmente
    en SHIPPING_HUB_CREDENTIAL_KEYS mientras se realiza
    la rotación.
    """

    value = str(encrypted_value or "").strip()

    if not value.startswith(CREDENTIAL_VALUE_PREFIX):
        raise CredentialDecryptionError(
            ("El contenido no utiliza el formato " "seguro de credenciales de WMS360.")
        )

    encrypted_token = value[len(CREDENTIAL_VALUE_PREFIX) :]

    try:
        rotated_token = (
            get_credentials_cipher()
            .rotate(encrypted_token.encode("ascii"))
            .decode("ascii")
        )

    except (
        InvalidToken,
        UnicodeEncodeError,
    ) as error:
        raise CredentialDecryptionError(
            ("No fue posible rotar las " "credenciales cifradas.")
        ) from error

    return CREDENTIAL_VALUE_PREFIX + rotated_token


# ============================================================
# ENMASCARADO PARA INTERFAZ
# ============================================================


def mask_secret_value(
    value: Any,
) -> str:
    """
    Oculta un secreto para mostrar solamente
    que existe una configuración.

    Nunca devuelve el valor completo.
    """

    text = str(value or "")

    if not text:
        return ""

    if len(text) <= 8:
        return MASKED_VALUE

    return MASKED_VALUE + text[-4:]


def mask_credentials(
    credentials: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Enmascara recursivamente un diccionario
    antes de utilizarlo en una interfaz.
    """

    if not isinstance(credentials, Mapping):
        return {}

    masked = {}

    for key, value in credentials.items():
        clean_key = str(key).strip()

        if isinstance(value, Mapping):
            masked[clean_key] = mask_credentials(value)

        elif isinstance(value, list):
            masked[clean_key] = [mask_secret_value(item) for item in value]

        else:
            masked[clean_key] = mask_secret_value(value)

    return masked


# ============================================================
# COMPROBACIONES
# ============================================================


def is_wms360_encrypted_value(
    encrypted_value: str,
) -> bool:
    """
    Indica si el texto tiene el prefijo del
    contenedor seguro de WMS360.
    """

    return str(encrypted_value or "").strip().startswith(CREDENTIAL_VALUE_PREFIX)


def credentials_are_readable(
    encrypted_value: str,
) -> bool:
    """
    Comprueba que un contenedor puede descifrarse
    sin exponer su contenido.
    """

    try:
        credentials = decrypt_credentials(encrypted_value)

    except (
        CredentialDecryptionError,
        CredentialValidationError,
        CredentialConfigurationError,
    ):
        return False

    return bool(credentials)
