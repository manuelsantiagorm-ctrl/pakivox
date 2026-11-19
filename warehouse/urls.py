from django.urls import path
from accounts.capability_access import capability_required
from accounts.activity_route_authorization import activity_route_required

from . import transfer_views, views

app_name = "warehouse"

urlpatterns = [
    path("traspasos/", transfer_views.transfer_list, name="transfer_list"),
    path("traspasos/nuevo/", transfer_views.transfer_create, name="transfer_create"),
    path("traspasos/<int:pk>/", transfer_views.transfer_detail, name="transfer_detail"),
    path("traspasos/<int:pk>/solicitar/", transfer_views.transfer_request, name="transfer_request"),
    path("traspasos/<int:pk>/reservar/", transfer_views.transfer_reserve, name="transfer_reserve"),
    path("traspasos/<int:pk>/cancelar/", transfer_views.transfer_cancel, name="transfer_cancel"),
    path("traspasos/<int:pk>/iniciar-despacho/", transfer_views.transfer_start_dispatch, name="transfer_start_dispatch"),
    path("traspasos/<int:pk>/confirmar-despacho/", transfer_views.transfer_dispatch_page, name="transfer_dispatch_page"),
    path("traspasos/<int:pk>/despachar/", transfer_views.transfer_dispatch, name="transfer_dispatch"),
    path("traspasos/<int:pk>/iniciar-recepcion/", transfer_views.transfer_start_receiving, name="transfer_start_receiving"),
    path("traspasos/<int:pk>/recibir/", transfer_views.transfer_receive, name="transfer_receive"),
    path("traspasos/<int:pk>/completar/", transfer_views.transfer_complete, name="transfer_complete"),
    path("traspasos/<int:pk>/reversa/", transfer_views.transfer_reverse, name="transfer_reverse"),

    path("pendientes-por-enviar/", views.lista_picking, name="pendientes_por_enviar"),
    path("listos-despacho/", views.lista_shipping, name="listos_despacho"),
    path("en-ruta/", views.lista_shipping, name="pedidos_en_ruta"),
    path("ubicaciones/", views.mapa_ubicaciones_qr, name="mapa_ubicaciones_qr"),
    path("ubicaciones/lista/", views.lista_ubicaciones, name="lista_ubicaciones"),
    path("ubicaciones/crear/", views.crear_ubicacion, name="crear_ubicacion"),
    path("ubicaciones/<int:pk>/editar/", views.editar_ubicacion, name="editar_ubicacion"),
    path("ubicaciones/asignar-stock/", views.asignar_stock_ubicacion, name="asignar_stock_ubicacion"),
    path("ubicaciones/<int:location_id>/asignar-stock/", views.asignar_stock_ubicacion, name="asignar_stock_ubicacion_location"),
    path("ubicaciones/ubicar-mercancia/", views.ubicar_mercancia, name="ubicar_mercancia"),
    path("picking/", views.lista_picking, name="lista_picking"),
    path("picking/generar/<int:order_id>/", views.generar_picking, name="generar_picking"),
    path("picking/<int:picking_id>/", views.detalle_picking, name="detalle_picking"),
    path("picking/<int:picking_id>/cancelar/", views.cancelar_picking, name="cancelar_picking"),
    path("picking/item/<int:item_id>/confirmar/", views.confirmar_item_picking, name="confirmar_item_picking"),
    path("picking/item/<int:item_id>/danado/", views.reportar_item_danado, name="reportar_item_danado"),
    path("packing/", views.lista_packing, name="lista_packing"),
    path("packing/generar/<int:picking_id>/", views.generar_packing, name="generar_packing"),
    path("packing/<int:packing_id>/", views.detalle_packing, name="detalle_packing"),
    path("packing/<int:packing_id>/completar/", views.completar_packing, name="completar_packing"),
    path("shipping/", views.lista_shipping, name="lista_shipping"),
    path("shipping/generar/<int:packing_id>/", views.generar_shipping, name="generar_shipping"),
    path("shipping/<int:shipping_id>/", views.detalle_shipping, name="detalle_shipping"),
    path("shipping/<int:shipping_id>/enviar/", views.marcar_enviado, name="marcar_enviado"),
    path("shipping/<int:shipping_id>/entregar/", views.marcar_entregado, name="marcar_entregado"),
    path("auditoria/", views.auditoria_warehouse, name="auditoria_warehouse"),
    path("trazabilidad/lotes/", views.lot_traceability_list, name="lot_traceability_list"),
    path("trazabilidad/lotes/<int:lot_id>/", views.lot_traceability_detail, name="lot_traceability_detail"),
    path("trazabilidad/lotes/<int:lot_id>/estado/", views.lot_status_update, name="lot_status_update"),
    path("trazabilidad/lotes/<int:lot_id>/devolucion-cuarentena/", views.lot_return_quarantine, name="lot_return_quarantine"),
    path("trazabilidad/saldos/<int:balance_id>/cuarentena/", views.lot_balance_quarantine, name="lot_balance_quarantine"),
    path("trazabilidad/saldos/<int:balance_id>/liberar-cuarentena/", views.lot_quarantine_release, name="lot_quarantine_release"),
    path("trazabilidad/saldos/<int:balance_id>/desechar/", views.lot_quarantine_waste, name="lot_quarantine_waste"),
    path("pedido/<int:order_id>/timeline/", views.timeline_pedido, name="timeline_pedido"),
    path("picking/<int:picking_id>/modo-pickeo/", views.modo_pickeo, name="modo_pickeo"),
    path("picking/item/<int:item_id>/modo-confirmar/", views.modo_pickeo_confirmar_item, name="modo_pickeo_confirmar_item"),
    path("layout/", views.layout_designer, name="layout_designer"),
    path("layout/guardar/", views.guardar_layout, name="guardar_layout"),
    path("layout/locations/", views.layout_locations_api, name="layout_locations_api"),
]

PS3B_ACTIVITY_READ_ROUTES = {
    "pendientes_por_enviar",
    "lista_picking",
    "detalle_picking",
    "lista_packing",
    "detalle_packing",
    "listos_despacho",
    "pedidos_en_ruta",
    "lista_shipping",
    "detalle_shipping",
    "timeline_pedido",
}

VIEW_ROUTES = {"lot_traceability_list", "lot_traceability_detail"}
PICKING_EXECUTE_ROUTES = {
    "generar_picking",
    "cancelar_picking",
    "confirmar_item_picking",
    "reportar_item_danado",
    "modo_pickeo",
    "modo_pickeo_confirmar_item",
}
PACKING_EXECUTE_ROUTES = {
    "generar_packing",
    "completar_packing",
}
SHIPPING_EXECUTE_ROUTES = {
    "generar_shipping",
    "marcar_enviado",
    "marcar_entregado",
}

for pattern in urlpatterns:
    if pattern.name in PS3B_ACTIVITY_READ_ROUTES:
        pattern.callback = activity_route_required(
            "warehouse",
            pattern.name,
        )(pattern.callback)
        continue

    if pattern.name in VIEW_ROUTES:
        capability = "inventory.view"
    elif pattern.name in PICKING_EXECUTE_ROUTES:
        capability = "picking.execute"
    elif pattern.name in PACKING_EXECUTE_ROUTES:
        capability = "packing.execute"
    elif pattern.name in SHIPPING_EXECUTE_ROUTES:
        capability = "shipping.execute"
    else:
        capability = "inventory.manage"

    pattern.callback = capability_required(capability)(pattern.callback)
