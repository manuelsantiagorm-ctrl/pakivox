from django.contrib.auth.decorators import login_required
from django.urls import path

from .access import payment_capability_required

from . import views



def protect(capability_code, view_function):
    return payment_capability_required(capability_code)(view_function)

app_name = "payments"

urlpatterns = [
    path(
        "",
        protect("payments.view", views.lista_pagos),
        name="lista_pagos",
    ),
    path(
        "registrar/",
        protect("payments.create", views.registrar_pago),
        name="registrar_pago",
    ),
    path(
        "<int:payment_id>/",
        protect("payments.view", views.detalle_pago),
        name="detalle_pago",
    ),
    path(
        "<int:payment_id>/comprobante/",
        protect("payments.proof.view", views.ver_comprobante_pago),
        name="ver_comprobante_pago",
    ),
    path(
        "<int:payment_id>/aprobar/",
        protect("payments.approve", views.aprobar_pago),
        name="aprobar_pago",
    ),
    path(
        "<int:payment_id>/rechazar/",
        protect("payments.approve", views.rechazar_pago),
        name="rechazar_pago",
    ),
    path(
        "<int:payment_id>/cancelar/",
        protect("payments.cancel", views.cancelar_pago),
        name="cancelar_pago",
    ),
    path(
        "<int:payment_id>/reembolsar/",
        protect("payments.refund", views.reembolsar_pago),
        name="reembolsar_pago",
    ),
    path(
        "reembolsos/<int:refund_id>/confirmar/",
        protect("payments.refund.confirm", views.confirmar_reembolso_pago),
        name="confirmar_reembolso_pago",
    ),
    path(
        "reembolsos/<int:refund_id>/saldo-a-favor/",
        protect("payments.customer_credit.manage", views.convertir_reembolso_saldo_favor),
        name="convertir_reembolso_saldo_favor",
    ),
    path(
        "pedido/<int:order_id>/saldo-a-favor/aplicar/",
        protect("payments.customer_credit.apply", views.aplicar_saldo_favor_pedido),
        name="aplicar_saldo_favor_pedido",
    ),
    path(
        "pedido/<int:order_id>/",
        protect("payments.view", views.pagos_pedido),
        name="pagos_pedido",
    ),
    path(
        "pedido/<int:order_id>/open-floating-chat/",
        protect("payments.proof.view", views.abrir_chat_flotante_pago),
        name="abrir_chat_flotante_pago",
    ),
]
