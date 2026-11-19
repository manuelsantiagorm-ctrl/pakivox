from functools import wraps

from django.core.exceptions import PermissionDenied
from django.db.models import F
from django.shortcuts import get_object_or_404

from accounts.capability_access import authorize_request
from accounts.warehouse_access import ACCESS_VIEW, get_user_company

from .models import GoodsReceipt, PurchaseOrder, SupplierPayment


def require_request_company(request):
    """
    Obtiene exclusivamente la empresa activa del trabajador.
    Nunca usa fallbacks a otra empresa.
    """
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied(
            "No se detectó una empresa activa para el usuario."
        )

    return company


def purchase_order_required(
    capability_code,
    *,
    required_level=ACCESS_VIEW,
):
    """
    Protege una vista que recibe purchase_order_id.

    Valida:
    usuario
    + empresa
    + capacidad
    + bodega de la OC
    + nivel de acceso
    """

    def decorator(view_function):
        @wraps(view_function)
        def wrapped_view(request, *args, **kwargs):
            company = require_request_company(request)

            purchase_order_id = kwargs.get("purchase_order_id")

            purchase_order = get_object_or_404(
                PurchaseOrder.objects.select_related(
                    "company",
                    "supplier",
                    "warehouse",
                ),
                pk=purchase_order_id,
                company=company,
                supplier__company=company,
                warehouse__company=company,
            )

            authorize_request(
                request,
                capability_code,
                required_level=required_level,
                warehouse=purchase_order.warehouse,
            )

            request.wms360_purchase_order = purchase_order

            return view_function(
                request,
                *args,
                **kwargs,
            )

        return wrapped_view

    return decorator


def goods_receipt_required(
    capability_code,
    *,
    required_level=ACCESS_VIEW,
):
    """
    Protege una vista que recibe receipt_id.

    Además verifica que:
    recepción
    + OC
    + bodega
    pertenezcan al mismo tenant.
    """

    def decorator(view_function):
        @wraps(view_function)
        def wrapped_view(request, *args, **kwargs):
            company = require_request_company(request)

            receipt_id = kwargs.get("receipt_id")

            receipt = get_object_or_404(
                GoodsReceipt.objects.select_related(
                    "company",
                    "warehouse",
                    "purchase_order",
                    "purchase_order__warehouse",
                ).filter(
                    purchase_order__warehouse_id=F("warehouse_id"),
                ),
                pk=receipt_id,
                company=company,
                warehouse__company=company,
                purchase_order__company=company,
            )

            authorize_request(
                request,
                capability_code,
                required_level=required_level,
                warehouse=receipt.warehouse,
            )

            request.wms360_goods_receipt = receipt

            return view_function(
                request,
                *args,
                **kwargs,
            )

        return wrapped_view

    return decorator


def supplier_payment_required(
    capability_code,
    *,
    required_level=ACCESS_VIEW,
):
    """
    Protege pagos a proveedores usando la bodega
    de la orden de compra asociada.
    """

    def decorator(view_function):
        @wraps(view_function)
        def wrapped_view(request, *args, **kwargs):
            company = require_request_company(request)

            payment_id = kwargs.get("payment_id")

            payment = get_object_or_404(
                SupplierPayment.objects.select_related(
                    "company",
                    "supplier",
                    "purchase_order",
                    "purchase_order__warehouse",
                ).filter(
                    purchase_order__supplier_id=F("supplier_id"),
                ),
                pk=payment_id,
                company=company,
                supplier__company=company,
                purchase_order__company=company,
                purchase_order__warehouse__company=company,
            )

            authorize_request(
                request,
                capability_code,
                required_level=required_level,
                warehouse=payment.purchase_order.warehouse,
            )

            request.wms360_supplier_payment = payment

            return view_function(
                request,
                *args,
                **kwargs,
            )

        return wrapped_view

    return decorator
