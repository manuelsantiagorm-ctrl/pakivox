from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .warehouse_access import set_active_warehouse


@login_required
@require_POST
def seleccionar_bodega_activa(request):
    selected_value = request.POST.get(
        "warehouse",
        "",
    )

    warehouse = set_active_warehouse(
        request=request,
        selected_value=selected_value,
    )

    if warehouse:
        messages.success(
            request,
            f"Bodega activa: {warehouse.name}.",
        )
    else:
        messages.info(
            request,
            "Vista global activada. Todas las bodegas estarán en modo consulta.",
        )

    next_url = request.POST.get(
        "next",
        "/",
    )

    is_safe_url = url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    )

    if not is_safe_url:
        next_url = "/"

    return redirect(next_url)
