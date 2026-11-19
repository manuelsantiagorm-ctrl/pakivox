"""
Servicio seguro para WhatsApp Embedded Signup de WMS360.

Responsabilidades de esta primera fase:
- validar la configuración global de Meta;
- impedir que App Secret llegue al navegador;
- preparar los datos públicos para el SDK;
- validar origen y callback HTTPS;
- no modificar canales, empresas, bodegas ni credenciales.

La integración con Graph API se agregará en la siguiente fase.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from django.conf import settings


META_VERSION_PATTERN = re.compile(
    r"^v\d+\.\d+$"
)


class MetaEmbeddedSignupError(Exception):
    """
    Error base de Embedded Signup.
    """


class MetaEmbeddedSignupConfigurationError(
    MetaEmbeddedSignupError
):
    """
    La configuración global de Meta es inválida o incompleta.
    """


@dataclass(frozen=True)
class MetaEmbeddedSignupSettings:
    app_id: str
    app_secret: str
    config_id: str
    platform_business_id: str
    platform_system_user_id: str
    api_version: str
    origin: str
    callback_url: str
    enabled: bool


def _clean_setting(name):
    return str(
        getattr(
            settings,
            name,
            "",
        )
        or ""
    ).strip()


def _required_setting(name):
    value = _clean_setting(name)

    if not value:
        raise MetaEmbeddedSignupConfigurationError(
            f"Falta la configuración obligatoria {name}."
        )

    return value


def _required_numeric_setting(name):
    value = _required_setting(name)

    if not value.isdigit():
        raise MetaEmbeddedSignupConfigurationError(
            f"La configuración {name} debe ser numérica."
        )

    return value


def _validate_https_url(
    *,
    name,
    value,
    allow_path,
):
    parsed = urlparse(value)

    if parsed.scheme != "https":
        raise MetaEmbeddedSignupConfigurationError(
            f"{name} debe usar HTTPS."
        )

    if not parsed.netloc:
        raise MetaEmbeddedSignupConfigurationError(
            f"{name} no contiene un dominio válido."
        )

    if parsed.username or parsed.password:
        raise MetaEmbeddedSignupConfigurationError(
            f"{name} no puede incluir credenciales."
        )

    if parsed.fragment:
        raise MetaEmbeddedSignupConfigurationError(
            f"{name} no puede incluir fragmentos."
        )
    if not allow_path and parsed.path not in {
            "",
            "/",
            }:
            raise MetaEmbeddedSignupConfigurationError(
                f"{name} debe contener solamente el origen."
                )
    if allow_path:
        return value
    return value.rstrip("/")


def get_embedded_signup_settings():
    """
    Devuelve la configuración completa para uso exclusivo
    del servidor.

    Nunca debe enviarse esta estructura completa al template,
    porque contiene META_APP_SECRET.
    """

    enabled = bool(
        getattr(
            settings,
            "META_EMBEDDED_SIGNUP_ENABLED",
            False,
        )
    )

    if not enabled:
        raise MetaEmbeddedSignupConfigurationError(
            "Embedded Signup está deshabilitado."
        )

    # Migracion a Twilio (25-sep-2026): Embedded Signup de Meta crea
    # numeros de Meta directo, asi que se apaga junto con Meta directo
    # aunque META_EMBEDDED_SIGNUP_ENABLED siga encendido.
    from .provider_policy import meta_direct_new_connections_enabled

    if not meta_direct_new_connections_enabled():
        raise MetaEmbeddedSignupConfigurationError(
            "La conexión directa con Meta está deshabilitada."
        )

    app_id = _required_numeric_setting(
        "META_APP_ID"
    )

    app_secret = _required_setting(
        "META_APP_SECRET"
    )

    config_id = _required_numeric_setting(
        "META_EMBEDDED_SIGNUP_CONFIG_ID"
    )

    platform_business_id = _required_numeric_setting(
        "META_PLATFORM_BUSINESS_ID"
    )

    platform_system_user_id = _required_numeric_setting(
        "META_PLATFORM_SYSTEM_USER_ID"
    )

    api_version = _required_setting(
        "META_GRAPH_API_VERSION"
    )

    if not META_VERSION_PATTERN.fullmatch(
        api_version
    ):
        raise MetaEmbeddedSignupConfigurationError(
            "META_GRAPH_API_VERSION no tiene un formato válido."
        )

    origin = _validate_https_url(
        name="META_EMBEDDED_SIGNUP_ORIGIN",
        value=_required_setting(
            "META_EMBEDDED_SIGNUP_ORIGIN"
        ),
        allow_path=False,
    )

    callback_url = _validate_https_url(
        name="META_EMBEDDED_SIGNUP_CALLBACK_URL",
        value=_required_setting(
            "META_EMBEDDED_SIGNUP_CALLBACK_URL"
        ),
        allow_path=True,
    )

    origin_host = urlparse(
        origin
    ).netloc.lower()

    callback_host = urlparse(
        callback_url
    ).netloc.lower()

    if origin_host != callback_host:
        raise MetaEmbeddedSignupConfigurationError(
            "El callback de Embedded Signup debe pertenecer "
            "al mismo dominio configurado como origen."
        )

    return MetaEmbeddedSignupSettings(
        app_id=app_id,
        app_secret=app_secret,
        config_id=config_id,
        platform_business_id=platform_business_id,
        platform_system_user_id=platform_system_user_id,
        api_version=api_version,
        origin=origin,
        callback_url=callback_url,
        enabled=enabled,
    )


def get_public_embedded_signup_context():
    """
    Devuelve únicamente valores seguros para el navegador.

    Nunca incluye:
    - App Secret;
    - token del usuario del sistema;
    - tokens de clientes;
    - credenciales cifradas de canales.
    """

    configuration = (
        get_embedded_signup_settings()
    )

    return {
        "app_id": configuration.app_id,
        "config_id": configuration.config_id,
        "api_version": configuration.api_version,
        "origin": configuration.origin,
        "callback_url": configuration.callback_url,
        "enabled": configuration.enabled,
    }
