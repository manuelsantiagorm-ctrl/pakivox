from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from accounts.capability_access import (
    get_active_user_profile,
    get_user_capability_codes,
)
from accounts.today_work import build_today_work_context
from notifications.access import notifications_for_request
from notifications.models import Notification


@login_required(login_url="/accounts/login/")
def employee_home(request):
    profile = get_active_user_profile(request.user)

    if profile is None:
        return redirect("accounts:logout")

    if request.user.is_superuser:
        return redirect("owner:console")

    if profile.role == "owner":
        return redirect("dashboard:ceo_dashboard")

    capability_codes = set(get_user_capability_codes(request.user))

    now = timezone.localtime()
    company = profile.company

    today_work_context = build_today_work_context(
        request,
        capability_codes=capability_codes,
    )

    notifications = (
        notifications_for_request(request)
        .filter(
            status=Notification.STATUS_ACTIVE,
        )
        .exclude(
            user_states__user=request.user,
            user_states__is_read=True,
        )
        .order_by(
            "-updated_at",
            "-pk",
        )
    )

    notification_count = notifications.count()
    notification_items = list(notifications[:5])

    full_name = request.user.get_full_name().strip() or request.user.get_username()
    first_name = full_name.split()[0]

    context = {
        "first_name": first_name,
        "full_name": full_name,
        "profile": profile,
        "company": company,
        "today": now,
        "notification_count": notification_count,
        "notification_items": notification_items,
        **today_work_context,
    }

    return render(
        request,
        "accounts/employee_home.html",
        context,
    )
