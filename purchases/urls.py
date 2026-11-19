from django.contrib.auth.decorators import login_required
from django.urls import path
from django.views.decorators.http import require_POST

from accounts.capability_access import capability_required
from accounts.warehouse_access import (
    ACCESS_APPROVE,
    ACCESS_MANAGE,
    ACCESS_OPERATE,
    ACCESS_VIEW,
)

from . import (
    receiving_views,
    replenishment_views,
    views,
)
from .access import (
    goods_receipt_required,
    purchase_order_required,
    supplier_payment_required,
)


app_name = "purchases"

LOGIN_URL = "/accounts/login/"


def secure(view, capability_code, *, post=False):
    wrapped = capability_required(capability_code)(view)

    if post:
        wrapped = require_POST(wrapped)

    return login_required(
        wrapped,
        login_url=LOGIN_URL,
    )


def secure_purchase_order(
    view,
    capability_code,
    *,
    required_level=ACCESS_VIEW,
    post=False,
):
    wrapped = purchase_order_required(
        capability_code,
        required_level=required_level,
    )(view)

    if post:
        wrapped = require_POST(wrapped)

    return login_required(
        wrapped,
        login_url=LOGIN_URL,
    )


def secure_receipt(
    view,
    capability_code,
    *,
    required_level=ACCESS_VIEW,
    post=False,
):
    wrapped = goods_receipt_required(
        capability_code,
        required_level=required_level,
    )(view)

    if post:
        wrapped = require_POST(wrapped)

    return login_required(
        wrapped,
        login_url=LOGIN_URL,
    )


def secure_payment(
    view,
    capability_code,
    *,
    required_level=ACCESS_VIEW,
    post=False,
):
    wrapped = supplier_payment_required(
        capability_code,
        required_level=required_level,
    )(view)

    if post:
        wrapped = require_POST(wrapped)

    return login_required(
        wrapped,
        login_url=LOGIN_URL,
    )


urlpatterns = [
    # =========================================================
    # COMPRAS
    # =========================================================
    path(
        "",
        secure(
            views.lista_compras,
            "purchases.view",
        ),
        name="lista_compras",
    ),
    path(
        "reabastecimiento/alertas/<int:alert_id>/",
        secure(
            replenishment_views.replenishment_alert_detail,
            "purchases.create",
        ),
        name="replenishment_alert_detail",
    ),
    # =========================================================
    # PROVEEDORES
    # =========================================================
    path(
        "proveedores/",
        secure(
            views.lista_proveedores,
            "suppliers.view",
        ),
        name="lista_proveedores",
    ),
    path(
        "proveedores/nuevo/",
        secure(
            views.crear_proveedor,
            "suppliers.manage",
        ),
        name="crear_proveedor",
    ),
    # =========================================================
    # ÓRDENES DE COMPRA
    # =========================================================
    path(
        "ordenes/nueva/",
        secure(
            views.crear_orden_compra,
            "purchases.create",
        ),
        name="crear_orden_compra",
    ),
    path(
        "ordenes/<int:purchase_order_id>/",
        secure_purchase_order(
            views.detalle_orden_compra,
            "purchases.view",
            required_level=ACCESS_VIEW,
        ),
        name="detalle_orden_compra",
    ),
    path(
        "ordenes/<int:purchase_order_id>/agregar-producto/",
        secure_purchase_order(
            views.agregar_producto_orden,
            "purchases.edit",
            required_level=ACCESS_OPERATE,
            post=True,
        ),
        name="agregar_producto_orden",
    ),
    path(
        "ordenes/<int:purchase_order_id>/enviar/",
        secure_purchase_order(
            views.enviar_orden_compra,
            "purchases.approve",
            required_level=ACCESS_APPROVE,
            post=True,
        ),
        name="enviar_orden_compra",
    ),
    path(
        "ordenes/<int:purchase_order_id>/cancelar/",
        secure_purchase_order(
            views.cancelar_orden_compra,
            "purchases.cancel",
            required_level=ACCESS_APPROVE,
            post=True,
        ),
        name="cancelar_orden_compra",
    ),
    path(
        "ordenes/<int:purchase_order_id>/items/<int:item_id>/actualizar/",
        secure_purchase_order(
            views.actualizar_producto_orden,
            "purchases.edit",
            required_level=ACCESS_OPERATE,
            post=True,
        ),
        name="actualizar_producto_orden",
    ),
    path(
        "ordenes/<int:purchase_order_id>/items/<int:item_id>/eliminar/",
        secure_purchase_order(
            views.eliminar_producto_orden,
            "purchases.edit",
            required_level=ACCESS_OPERATE,
            post=True,
        ),
        name="eliminar_producto_orden",
    ),
    # =========================================================
    # RECEPCIONES
    # =========================================================
    path(
        "ordenes/<int:purchase_order_id>/crear-recepcion/",
        secure_purchase_order(
            views.crear_recepcion,
            "receiving.execute",
            required_level=ACCESS_OPERATE,
            post=True,
        ),
        name="crear_recepcion",
    ),
    path(
        "recepciones/",
        secure(
            receiving_views.lista_recepciones,
            "receiving.view",
        ),
        name="lista_recepciones",
    ),
    path(
        "recepciones/<int:receipt_id>/",
        secure_receipt(
            views.detalle_recepcion,
            "receiving.view",
            required_level=ACCESS_VIEW,
        ),
        name="detalle_recepcion",
    ),
    path(
        "recepciones/<int:receipt_id>/actualizar-item/<int:item_id>/",
        secure_receipt(
            views.actualizar_item_recepcion,
            "receiving.execute",
            required_level=ACCESS_OPERATE,
            post=True,
        ),
        name="actualizar_item_recepcion",
    ),
    path(
        "recepciones/<int:receipt_id>/confirmar/",
        secure_receipt(
            views.confirmar_recepcion,
            "receiving.confirm",
            required_level=ACCESS_APPROVE,
            post=True,
        ),
        name="confirmar_recepcion",
    ),
    path(
        "recepciones/<int:receipt_id>/cancelar/",
        secure_receipt(
            views.cancelar_recepcion,
            "receiving.cancel",
            required_level=ACCESS_MANAGE,
            post=True,
        ),
        name="cancelar_recepcion",
    ),
    # =========================================================
    # PAGOS A PROVEEDOR
    # =========================================================
    path(
        "ordenes/<int:purchase_order_id>/pagos/registrar/",
        secure_purchase_order(
            views.registrar_pago_proveedor,
            "supplier_payments.create",
            required_level=ACCESS_OPERATE,
            post=True,
        ),
        name="registrar_pago_proveedor",
    ),
    path(
        "pagos-proveedor/<int:payment_id>/aprobar/",
        secure_payment(
            views.aprobar_pago_proveedor,
            "supplier_payments.approve",
            required_level=ACCESS_APPROVE,
            post=True,
        ),
        name="aprobar_pago_proveedor",
    ),
    path(
        "pagos-proveedor/<int:payment_id>/rechazar/",
        secure_payment(
            views.rechazar_pago_proveedor,
            "supplier_payments.reject",
            required_level=ACCESS_APPROVE,
            post=True,
        ),
        name="rechazar_pago_proveedor",
    ),
    path(
        "pagos-proveedor/<int:payment_id>/cancelar/",
        secure_payment(
            views.cancelar_pago_proveedor,
            "supplier_payments.cancel",
            required_level=ACCESS_MANAGE,
            post=True,
        ),
        name="cancelar_pago_proveedor",
    ),
    path(
        "pagos-proveedor/<int:payment_id>/comprobante/",
        secure_payment(
            views.ver_comprobante_pago_proveedor,
            "supplier_payments.proof.view",
            required_level=ACCESS_VIEW,
        ),
        name="ver_comprobante_pago_proveedor",
    ),
]
