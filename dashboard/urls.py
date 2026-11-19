from django.urls import path

from . import views

app_name = "dashboard"


urlpatterns = [
    path(
        "",
        views.ceo_dashboard,
        name="ceo_dashboard",
    ),
    path(
        "control-flow/",
        views.control_flow_dashboard,
        name="control_flow_dashboard",
    ),
    path(
        "ia/",
        views.ceo_ai_query,
        name="ceo_ai_query",
    ),
    path(
        "ia/exportar-excel/",
        views.ceo_ai_export_excel,
        name="ceo_ai_export_excel",
    ),
]
