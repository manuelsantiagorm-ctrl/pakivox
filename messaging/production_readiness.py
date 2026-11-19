"""Comprobaciones de despliegue seguras y reutilizables."""

from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Error, Warning
from django.db import connection


PLACEHOLDER_PARTS = ("change-me", "changeme", "example", "replace", "dummy")


def _secret(name):
    return str(getattr(settings, name, "") or "").strip()


def _is_placeholder(value):
    lowered = value.lower()
    return any(part in lowered for part in PLACEHOLDER_PARTS)


def collect_production_issues(require_ycloud=None, check_database=False):
    issues = []
    production = not bool(getattr(settings, "DEBUG", True))
    ycloud_required = (
        bool(getattr(settings, "YCLOUD_ENABLED", False))
        if require_ycloud is None
        else bool(require_ycloud)
    )

    secret_key = _secret("SECRET_KEY")
    if len(secret_key) < 50 or _is_placeholder(secret_key):
        issues.append(Error("DJANGO_SECRET_KEY debe ser aleatoria y tener al menos 50 caracteres.", id="wms360.E001"))

    hosts = [str(v).strip() for v in getattr(settings, "ALLOWED_HOSTS", [])]
    if production and (not hosts or "*" in hosts):
        issues.append(Error("DJANGO_ALLOWED_HOSTS debe declarar dominios concretos.", id="wms360.E002"))

    origins = [str(v).strip() for v in getattr(settings, "CSRF_TRUSTED_ORIGINS", [])]
    if production and (not origins or any(urlparse(v).scheme != "https" for v in origins)):
        issues.append(Error("DJANGO_CSRF_TRUSTED_ORIGINS debe contener únicamente orígenes HTTPS.", id="wms360.E003"))

    for name in ("SECURE_SSL_REDIRECT", "SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE"):
        if production and not bool(getattr(settings, name, False)):
            issues.append(Error(f"{name} debe estar habilitado en producción.", id="wms360.E004"))

    if ycloud_required:
        api_key = _secret("YCLOUD_API_KEY")
        webhook_secret = _secret("YCLOUD_WEBHOOK_SECRET")
        if len(api_key) < 20 or _is_placeholder(api_key):
            issues.append(Error("YCLOUD_API_KEY falta o parece insegura.", id="wms360.E010"))
        if len(webhook_secret) < 32 or _is_placeholder(webhook_secret):
            issues.append(Error("YCLOUD_WEBHOOK_SECRET falta o parece inseguro.", id="wms360.E011"))
        base_url = _secret("YCLOUD_API_BASE_URL")
        if urlparse(base_url).scheme != "https":
            issues.append(Error("YCLOUD_API_BASE_URL debe usar HTTPS.", id="wms360.E012"))
    elif production:
        issues.append(Warning("YCloud permanece deshabilitado; el despliegue es seguro pero aún no enviará mensajes.", id="wms360.W010"))

    if check_database:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception:
            issues.append(Error("La base de datos no respondió al health check.", id="wms360.E020"))
    return issues


def public_health_payload(check_database=True):
    database_ok = True
    if check_database:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception:
            database_ok = False
    return {
        "status": "ok" if database_ok else "unavailable",
        "database": "ok" if database_ok else "unavailable",
    }
