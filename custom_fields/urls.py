from django.urls import path

from . import views

app_name = "custom_fields"

urlpatterns = [
    path("", views.field_list, name="field_list"),
    path("nuevo/", views.field_create, name="field_create"),
    path(
        "nuevo-desde-importador/",
        views.field_create_inline,
        name="field_create_inline",
    ),
    path("<int:pk>/editar/", views.field_edit, name="field_edit"),
    path("<int:pk>/estado/", views.field_toggle, name="field_toggle"),
]
