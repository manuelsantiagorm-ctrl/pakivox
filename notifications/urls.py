from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.notification_center, name="center"),
    path(
        "<int:notification_id>/open/",
        views.open_notification,
        name="open",
    ),
    path(
        "<int:notification_id>/mark-read/",
        views.mark_read,
        name="mark_read",
    ),
    path(
        "mark-all-read/",
        views.mark_all_read,
        name="mark_all_read",
    ),
]

