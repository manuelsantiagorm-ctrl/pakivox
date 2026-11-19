from django.urls import path

from shipping_hub import dispatch_views

app_name = "shipping_hub_dispatch"

urlpatterns = [
    path("", dispatch_views.dispatch_center, name="dispatch_center"),
    path(
        "envios/<int:shipment_id>/tracking/",
        dispatch_views.shipment_tracking_timeline,
        name="shipment_tracking_timeline",
    ),
    path(
        "envios/<int:shipment_id>/marcar-enviado/",
        dispatch_views.mark_dispatched,
        name="mark_dispatched",
    ),
    path(
        "envios/<int:shipment_id>/marcar-entregado/",
        dispatch_views.mark_delivered,
        name="mark_delivered",
    ),
]
