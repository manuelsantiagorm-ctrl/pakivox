"""Configuración aislada usada solamente por las pruebas del instalador legal."""

import tempfile

from config.settings import *  # noqa: F403,F401


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": tempfile.gettempdir() + "/pakivox_legal_installer_test.sqlite3",
    }
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
