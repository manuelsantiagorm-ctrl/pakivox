"""Ajustes de producción de WMS360.

Uso: DJANGO_SETTINGS_MODULE=config.settings_production
No contiene secretos; todos se leen del entorno.
"""

import os

from .settings import *  # noqa: F401,F403


def _bool(name, default=False):
    value = os.getenv(name, "1" if default else "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


DEBUG = False

ALLOWED_HOSTS = [
    item.strip()
    for item in os.getenv("DJANGO_ALLOWED_HOSTS", "").split(",")
    if item.strip()
]
CSRF_TRUSTED_ORIGINS = [
    item.strip().rstrip("/")
    for item in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if item.strip()
]

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
SECURE_SSL_REDIRECT = _bool("DJANGO_SECURE_SSL_REDIRECT", True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_SECURE_HSTS_SECONDS", "3600"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = _bool(
    "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", True
)
SECURE_HSTS_PRELOAD = _bool("DJANGO_SECURE_HSTS_PRELOAD", False)
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"

# Configuración vigente de django-allauth; elimina las advertencias antiguas.
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
for _deprecated_setting in (
    "ACCOUNT_AUTHENTICATION_METHOD",
    "ACCOUNT_EMAIL_REQUIRED",
    "ACCOUNT_USERNAME_REQUIRED",
):
    globals().pop(_deprecated_setting, None)

# Nunca habilitar YCloud por defecto al desplegar.
YCLOUD_ENABLED = _bool("YCLOUD_ENABLED", False)
YCLOUD_WEBHOOK_TOLERANCE_SECONDS = int(
    os.getenv("YCLOUD_WEBHOOK_TOLERANCE_SECONDS", "300")
)

DATABASES["default"]["CONN_MAX_AGE"] = int(
    os.getenv("DJANGO_CONN_MAX_AGE", "60")
)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO"},
        "messaging": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
