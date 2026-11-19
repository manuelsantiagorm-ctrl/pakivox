from django.conf import settings


def legal_identity(request):
    return {
        "legal_identity": {
            "operator_name": settings.LEGAL_OPERATOR_NAME,
            "trade_name": settings.LEGAL_TRADE_NAME,
            "rfc": settings.LEGAL_RFC,
            "address": settings.LEGAL_ADDRESS,
            "privacy_email": settings.LEGAL_PRIVACY_EMAIL,
            "support_email": settings.LEGAL_SUPPORT_EMAIL,
            "jurisdiction": settings.LEGAL_JURISDICTION,
        }
    }
