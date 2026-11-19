from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .production_readiness import public_health_payload


@never_cache
@require_GET
def health_live(request):
    return JsonResponse({"status": "ok"})


@never_cache
@require_GET
def health_ready(request):
    payload = public_health_payload(check_database=True)
    return JsonResponse(payload, status=200 if payload["status"] == "ok" else 503)
