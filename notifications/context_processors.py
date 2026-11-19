from accounts.warehouse_access import get_user_company

from .access import notifications_for_request
from .models import Notification


def _empty_notification_context(storage_key=""):
    return {
        "notification_unread_count": 0,
        "notification_dropdown_items": [],
        "notification_sound_id": None,
        "notification_sound_area": "",
        "notification_sound_level": "",
        "notification_sound_storage_key": storage_key,
    }


def notification_context(request):
    if not request.user.is_authenticated:
        return _empty_notification_context()

    company = get_user_company(request.user)

    if company is None:
        return _empty_notification_context()

    storage_key = f"wms360-notification-{company.pk}-{request.user.pk}"

    active = notifications_for_request(request).filter(
        status=Notification.STATUS_ACTIVE
    )

    unread = active.exclude(
        user_states__user=request.user,
        user_states__is_read=True,
    ).order_by("-updated_at", "-pk")

    latest_unread = unread.first()

    if latest_unread is None:
        return {
            **_empty_notification_context(storage_key),
            "notification_unread_count": 0,
            "notification_dropdown_items": [],
        }

    return {
        "notification_unread_count": unread.count(),
        "notification_dropdown_items": list(unread[:4]),
        "notification_sound_id": latest_unread.pk,
        "notification_sound_area": latest_unread.source_app or "",
        "notification_sound_level": latest_unread.level or "info",
        "notification_sound_storage_key": storage_key,
    }
