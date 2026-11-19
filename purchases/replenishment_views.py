from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render

from accounts.capability_access import authorize_request
from accounts.warehouse_access import (
    ACCESS_OPERATE,
    get_user_company,
)
from inventory.models import StockCriticalAlert


def replenishment_alert_detail(
    request,
    alert_id,
):
    """
    Vista operativa de reabastecimiento.

    No consulta proveedores historicos, costos, margen,
    pagos ni totales financieros.
    """
    company = get_user_company(
        request.user
    )

    if company is None:
        raise PermissionDenied(
            "No se detecto una empresa activa."
        )

    alert = get_object_or_404(
        (
            StockCriticalAlert.objects
            .select_related(
                "warehouse",
                "variant",
                "variant__product",
            )
            .only(
                "id",
                "company_id",
                "warehouse_id",
                "warehouse__id",
                "warehouse__company_id",
                "warehouse__name",
                "variant_id",
                "variant__id",
                "variant__company_id",
                "variant__sku",
                "variant__product_id",
                "variant__product__id",
                "variant__product__company_id",
                "variant__product__name",
                "alert_type",
                "status",
                "current_available",
                "min_stock",
                "reference",
                "message",
                "updated_at",
            )
        ),
        pk=alert_id,
        company_id=company.id,
        warehouse__company_id=company.id,
        variant__company_id=company.id,
        variant__product__company_id=company.id,
        status=StockCriticalAlert.STATUS_ACTIVE,
    )

    authorize_request(
        request,
        "purchases.create",
        required_level=ACCESS_OPERATE,
        warehouse=alert.warehouse,
    )

    return render(
        request,
        "purchases/replenishment_alert_detail.html",
        {
            "alert": alert,
        },
    )
