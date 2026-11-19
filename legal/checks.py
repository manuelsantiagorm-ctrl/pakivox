from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security, deploy=True)
def legal_identity_check(app_configs, **kwargs):
    if not getattr(settings, "APP_ENV", "development") == "production":
        return []
    required = {
        "LEGAL_OPERATOR_NAME": settings.LEGAL_OPERATOR_NAME,
        "LEGAL_RFC": settings.LEGAL_RFC,
        "LEGAL_ADDRESS": settings.LEGAL_ADDRESS,
        "LEGAL_PRIVACY_EMAIL": settings.LEGAL_PRIVACY_EMAIL,
        "LEGAL_SUPPORT_EMAIL": settings.LEGAL_SUPPORT_EMAIL,
    }
    missing = [name for name, value in required.items() if not str(value).strip()]
    if not missing:
        return []
    return [
        Error(
            "Falta completar la identidad legal pública de PAKIVOX.",
            hint="Configura: " + ", ".join(missing),
            id="legal.E001",
        )
    ]
