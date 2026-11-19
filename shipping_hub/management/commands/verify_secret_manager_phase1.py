from django.core.management.base import BaseCommand

from shipping_hub.models import (
    IntegrationSecret,
    IntegrationSecretAuditEvent,
    IntegrationSecretVersion,
)
from shipping_hub.services.secret_manager import (
    generate_secret,
    masked_hint,
    secret_fingerprint,
)


class Command(BaseCommand):
    help = "Verifica la estructura del Secret Manager sin crear secretos."

    def handle(self, *args, **options):
        required = {"integration", "name", "kind", "is_enabled"}
        fields = {field.name for field in IntegrationSecret._meta.get_fields()}
        assert not (required - fields)

        version_fields = {
            field.name for field in IntegrationSecretVersion._meta.get_fields()
        }
        assert {
            "secret",
            "version",
            "encrypted_value",
            "fingerprint_sha256",
            "masked_hint",
            "status",
        }.issubset(version_fields)

        value = generate_secret()
        assert len(value) >= 40
        assert len(secret_fingerprint(value)) == 64
        assert masked_hint("abcdef") == "****cdef"

        assert IntegrationSecretAuditEvent.ACTION_ROTATED == "rotated"

        self.stdout.write(
            self.style.SUCCESS(
                "SECRET MANAGER FASE 1 VERIFICADO: OK"
            )
        )
        self.stdout.write(
            "No se creó, leyó, rotó ni imprimió ningún secreto."
        )
