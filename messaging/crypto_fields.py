from cryptography.fernet import Fernet, InvalidToken

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

ENCRYPTED_PREFIX = "fernet$"


def _fernets():
    keys = getattr(settings, "MESSAGING_CREDENTIAL_KEYS", ())
    if isinstance(keys, str):
        keys = tuple(key.strip() for key in keys.split(",") if key.strip())

    if not keys:
        raise ImproperlyConfigured(
            "Falta MESSAGING_CREDENTIAL_KEYS. Configúrala en el archivo .env."
        )

    try:
        return tuple(Fernet(key.encode("utf-8")) for key in keys)
    except (TypeError, ValueError) as error:
        raise ImproperlyConfigured(
            "MESSAGING_CREDENTIAL_KEYS contiene una clave Fernet inválida."
        ) from error


def encrypt_credential(value):
    if value in (None, ""):
        return value

    value = str(value)
    if value.startswith(ENCRYPTED_PREFIX):
        return value

    token = _fernets()[0].encrypt(value.encode("utf-8")).decode("ascii")
    return f"{ENCRYPTED_PREFIX}{token}"


def decrypt_credential(value):
    if value in (None, ""):
        return value
    value = str(value)
    if not value.startswith(ENCRYPTED_PREFIX):
        raise ImproperlyConfigured(
            "Se encontró una credencial de messaging sin cifrar en la "
            "base de datos. Todas las credenciales deben estar cifradas "
            "con el prefijo 'fernet$'. Revisa la migración de datos "
            "antes de continuar (BUG-068)."
        )
    token = value[len(ENCRYPTED_PREFIX) :].encode("ascii")
    for fernet in _fernets():
        try:
            return fernet.decrypt(token).decode("utf-8")
        except InvalidToken:
            continue
    raise ImproperlyConfigured(
        "No fue posible descifrar una credencial de messaging con las claves actuales."
    )


class EncryptedTextField(models.TextField):
    description = "Texto cifrado con Fernet"

    def from_db_value(self, value, expression, connection):
        return decrypt_credential(value)

    def to_python(self, value):
        if value in (None, ""):
            return value
        value = str(value)
        if value.startswith(ENCRYPTED_PREFIX):
            return decrypt_credential(value)
        return value

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt_credential(value)
