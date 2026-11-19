from django.contrib.auth.decorators import login_required
from django.urls import path

from accounts.capability_access import capability_required
from customer_updates import views as customer_updates_views
from .models import Order, OrderItem, OrderResolutionEvent
from . import views


def protect(capability_code, view_function):
    return login_required(
        capability_required(capability_code)(view_function),
        login_url="/accounts/login/",
    )

app_name = "sales"


urlpatterns = [
    # ============================================================
    # PIPELINE COMERCIAL / VENTAS
    # ============================================================
    # Pedidos en curso · CS-1E (cola operativa principal)
    path(
        "orders/in-progress/",
        protect("sales.view", views.pedidos_en_curso),
        name="pedidos_en_curso",
    ),
    # Centro de incidencias · CS-1F
    path(
        "orders/incidents/",
        protect("sales.view", views.incidencias_pedidos),
        name="incidencias_pedidos",
    ),
    # Todos los pedidos
    path("orders/", protect("sales.view", views.lista_pedidos), name="lista_pedidos"),
    # Pedidos sin pagar
    path(
        "orders/sin-pagar/",
        protect("sales.view", views.lista_pedidos),
        name="pedidos_sin_pagar",
    ),
    path(
        "orders/<int:order_id>/mark-ready-for-payment/",
        protect("sales.create", views.marcar_pedido_listo_para_cobro),
        name="marcar_pedido_listo_para_cobro",
    ),
    path(
        "orders/<int:order_id>/return-to-capture/",
        protect("payments.approve", views.regresar_pedido_a_captura),
        name="regresar_pedido_a_captura",
    ),
    # Pedidos con comprobante en revisión
    path(
        "orders/en-revision-pago/",
        protect("payments.view", views.lista_pedidos),
        name="pedidos_en_revision_pago",
    ),
    # Pedidos aprobados / pagados
    path(
        "orders/aprobados/",
        protect("sales.view", views.lista_pedidos),
        name="pedidos_aprobados",
    ),
    # Crear pedido
    path(
        "orders/create/",
        protect("sales.create", views.crear_pedido),
        name="crear_pedido",
    ),
    # Detector server-side de pedidos activos/cancelados recientes del cliente.
    path(
        "orders/create/related-orders/",
        protect("sales.create", views.customer_related_orders_api),
        name="customer_related_orders_api",
    ),
    # Detalle del pedido
    path(
        "orders/<int:order_id>/",
        protect("sales.view", views.detalle_pedido),
        name="detalle_pedido",
    ),
    # Avisos al cliente sin API de WhatsApp (customer_updates)
    path(
        "orders/<int:order_id>/avisos/whatsapp/",
        protect("sales.create", customer_updates_views.abrir_whatsapp_pedido),
        name="abrir_whatsapp_pedido",
    ),
    path(
        "orders/<int:order_id>/avisos/correo/",
        protect("sales.create", customer_updates_views.enviar_correo_pedido),
        name="enviar_correo_pedido",
    ),
    path(
        "orders/<int:order_id>/avisos/link-nuevo/",
        protect("sales.create", customer_updates_views.renovar_link_seguimiento),
        name="renovar_link_seguimiento",
    ),
    # Agregar producto al pedido
    path(
        "orders/<int:order_id>/add-item/",
        protect("sales.create", views.agregar_producto_pedido),
        name="agregar_producto_pedido",
    ),
    # Eliminar producto del pedido
    path(
        "orders/<int:order_id>/delete-item/<int:item_id>/",
        protect("sales.create", views.eliminar_producto_pedido),
        name="eliminar_producto_pedido",
    ),
    # Actualizar estado del pedido
    path(
        "orders/<int:order_id>/update-status/",
        protect("sales.create", views.actualizar_estado_pedido),
        name="actualizar_estado_pedido",
    ),
    # Actualizar pago del pedido
    path(
        "orders/<int:order_id>/update-payment/",
        protect("payments.approve", views.actualizar_pago_pedido),
        name="actualizar_pago_pedido",
    ),
    # Marcar pedido como pagado
    path(
        "orders/<int:order_id>/mark-paid/",
        protect("payments.approve", views.marcar_pedido_pagado),
        name="marcar_pedido_pagado",
    ),
    # Actualizar producto del pedido
    path(
        "orders/<int:order_id>/update-item/<int:item_id>/",
        protect("sales.create", views.actualizar_producto_pedido),
        name="actualizar_producto_pedido",
    ),
    # Cancelar pedido
    path(
        "orders/<int:order_id>/cancel/",
        protect("sales.cancel_order", views.cancelar_pedido),
        name="cancelar_pedido",
    ),
    # Recuperar el mismo pedido por error humano, sin duplicar pagos.
    path(
        "orders/<int:order_id>/recover-human-error/",
        protect("sales.recover_order", views.recuperar_pedido_error_humano),
        name="recuperar_pedido_error_humano",
    ),
    # Cancelación comercial real de un pedido con dinero aprobado.
    path(
        "orders/<int:order_id>/commercial-cancel/",
        protect("sales.commercial_cancel", views.cancelar_pedido_comercialmente),
        name="cancelar_pedido_comercialmente",
    ),
    path(
        "orders/create/customer-quick/",
        protect("sales.create", views.crear_cliente_rapido_desde_pedido),
        name="crear_cliente_rapido_desde_pedido",
    ),
    path(
        "orders/customer/<int:customer_id>/open-whatsapp-chat/",
        protect("sales.view", views.abrir_chat_cliente_desde_pedido),
        name="abrir_chat_cliente_desde_pedido",
    ),
    path(
        "orders/<int:order_id>/open-floating-chat/",
        protect("sales.view", views.abrir_chat_flotante_pedido),
        name="abrir_chat_flotante_pedido",
    ),
    path(
        "pedidos/direcciones-cliente/",
        protect("sales.view", views.customer_shipping_addresses_api),
        name="customer_shipping_addresses_api",
    ),
    path(
        "pedidos/perfil-rapido-cliente/",
        protect("sales.view", views.customer_quick_profile_api),
        name="customer_quick_profile_api",
    ),
    path(
        "pedidos/nuevo/",
        protect("sales.create", views.crear_pedido),
        name="crear_pedido",
    ),
    path(
        "orders/<int:order_id>/variantes-buscar/",
        protect("sales.view", views.order_variant_search_api),
        name="order_variant_search_api",
    ),
]
