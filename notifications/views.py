from django.contrib import messages
from accounts.capability_access import capability_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils.http import url_has_allowed_host_and_scheme
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .access import notifications_for_request
from .models import Notification, NotificationUserState


def _mark_read(notification, user):
    NotificationUserState.objects.update_or_create(
        notification=notification,
        user=user,
        defaults={
            "is_read": True,
            "read_at": timezone.now(),
        },
    )


@login_required(login_url="/admin/login/")
def notification_center(request):
    queryset = notifications_for_request(request)
    status = request.GET.get("status", "active").strip()
    level = request.GET.get("level", "").strip()
    category = request.GET.get("category", "").strip()
    read_filter = request.GET.get("read", "").strip()
    q = request.GET.get("q", "").strip()

    if status in dict(Notification.STATUS_CHOICES):
        queryset = queryset.filter(status=status)
    if level in dict(Notification.LEVEL_CHOICES):
        queryset = queryset.filter(level=level)
    if category:
        queryset = queryset.filter(category=category)
    if q:
        queryset = queryset.filter(
            Q(title__icontains=q)
            | Q(message__icontains=q)
            | Q(reference__icontains=q)
        )

    read_ids = set(
        NotificationUserState.objects.filter(
            user=request.user,
            is_read=True,
            notification__in=queryset,
        ).values_list("notification_id", flat=True)
    )
    if read_filter == "read":
        queryset = queryset.filter(pk__in=read_ids)
    elif read_filter == "unread":
        queryset = queryset.exclude(pk__in=read_ids)

    page_obj = Paginator(queryset, 30).get_page(request.GET.get("page"))
    for item in page_obj.object_list:
        item.is_read_for_user = item.pk in read_ids

    active_base = notifications_for_request(request).filter(
        status=Notification.STATUS_ACTIVE,
    )
    unread_count = active_base.exclude(
        user_states__user=request.user,
        user_states__is_read=True,
    ).count()

    return render(
        request,
        "notifications/center.html",
        {
            "notifications": page_obj.object_list,
            "page_obj": page_obj,
            "status": status,
            "level": level,
            "category": category,
            "read_filter": read_filter,
            "q": q,
            "active_count": active_base.count(),
            "unread_count": unread_count,
        },
    )


@login_required(login_url="/admin/login/")
def open_notification(request, notification_id):
    notification = get_object_or_404(
        notifications_for_request(request),
        pk=notification_id,
    )
    _mark_read(notification, request.user)

    if not notification.action_name:
        return redirect("notifications:center")

    try:
        target = reverse(
            notification.action_name,
            kwargs=notification.action_kwargs or {},
        )
    except NoReverseMatch:
        messages.error(
            request,
            "La vista relacionada ya no está disponible.",
        )
        return redirect("notifications:center")

    if not target.startswith("/") or target.startswith("//"):
        raise PermissionDenied("Destino de notificación no permitido.")

    return redirect(target)


@login_required(login_url="/admin/login/")
@require_POST
def mark_read(request, notification_id):
    notification = get_object_or_404(
        notifications_for_request(request),
        pk=notification_id,
    )
    _mark_read(notification, request.user)

    # BUG-083: valida que "next" apunte a este mismo sitio antes de
    # redirigir, para evitar open redirect (phishing usando un link
    # que parece del dominio real pero termina en un sitio externo).
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)

    return redirect("notifications:center")


@login_required(login_url="/admin/login/")
@require_POST
def mark_all_read(request):
    notifications = notifications_for_request(request).filter(
        status=Notification.STATUS_ACTIVE,
    )
    now = timezone.now()
    states = [
        NotificationUserState(
            notification=notification,
            user=request.user,
            is_read=True,
            read_at=now,
        )
        for notification in notifications
    ]
    NotificationUserState.objects.bulk_create(
        states,
        ignore_conflicts=True,
    )
    NotificationUserState.objects.filter(
        notification__in=notifications,
        user=request.user,
    ).update(is_read=True, read_at=now)
    messages.success(request, "Notificaciones marcadas como leídas.")
    return redirect("notifications:center")
