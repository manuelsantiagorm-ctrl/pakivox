from django.contrib import admin
from companies.scoping import CompanyScopedAdmin

from .models import Notification, NotificationUserState


@admin.register(Notification)
class NotificationAdmin(CompanyScopedAdmin):
    list_display = (
        "title",
        "company",
        "warehouse",
        "level",
        "status",
        "updated_at",
    )
    list_filter = ("category", "level", "status")
    search_fields = ("title", "message", "reference", "dedupe_key")


@admin.register(NotificationUserState)
class NotificationUserStateAdmin(CompanyScopedAdmin):
    list_display = ("notification", "user", "is_read", "read_at")
    list_filter = ("is_read",)
    readonly_fields = ()
