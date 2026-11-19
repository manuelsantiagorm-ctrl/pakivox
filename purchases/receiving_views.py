from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.shortcuts import render

from accounts.capability_access import get_active_user_profile
from accounts.warehouse_access import (
    ACCESS_VIEW,
    get_accessible_warehouses,
)

from .models import GoodsReceipt


PAGE_SIZE = 30


def lista_recepciones(request):
    """
    Cola operativa de Recepcion.

    No entrega costos, pagos, margenes ni informacion contable.
    """
    profile = get_active_user_profile(
        request.user
    )

    if profile is None:
        raise PermissionDenied(
            "No se detecto una empresa activa."
        )

    company_id = profile.company_id

    warehouse_ids = (
        get_accessible_warehouses(
            user=request.user,
            required_level=ACCESS_VIEW,
        )
        .filter(
            company_id=company_id,
        )
        .values_list(
            "id",
            flat=True,
        )
    )

    receipts = (
        GoodsReceipt.objects
        .select_related(
            "warehouse",
            "purchase_order",
            "purchase_order__supplier",
        )
        .filter(
            company_id=company_id,
            warehouse_id__in=warehouse_ids,
            warehouse__company_id=company_id,
            purchase_order__company_id=company_id,
            purchase_order__warehouse_id=F(
                "warehouse_id"
            ),
            purchase_order__supplier__company_id=company_id,
        )
        .order_by(
            "-created_at",
            "-id",
        )
    )

    status = str(
        request.GET.get("status", "")
        or ""
    ).strip()

    valid_statuses = {
        value
        for value, _label
        in GoodsReceipt.STATUS_CHOICES
    }

    if status in valid_statuses:
        receipts = receipts.filter(
            status=status
        )
    else:
        status = ""

    q = str(
        request.GET.get("q", "")
        or ""
    ).strip()[:100]

    if q:
        receipts = receipts.filter(
            Q(folio__icontains=q)
            | Q(
                purchase_order__folio__icontains=q
            )
            | Q(
                purchase_order__supplier__name__icontains=q
            )
            | Q(
                warehouse__name__icontains=q
            )
        )

    paginator = Paginator(
        receipts,
        PAGE_SIZE,
    )

    page_obj = paginator.get_page(
        request.GET.get("page")
    )

    return render(
        request,
        "purchases/lista_recepciones.html",
        {
            "page_obj": page_obj,
            "receipts": page_obj.object_list,
            "status": status,
            "q": q,
            "status_choices": GoodsReceipt.STATUS_CHOICES,
        },
    )
