"""Contexto del panel "Avisar al cliente" del detalle del pedido."""

import sentry_sdk

from .drafts import build_drafts
from .emails import resolve_recipient
from .models import CustomerEmailNotification, WhatsAppDraftLog
from .phones import phone_candidates
from .services import customer_emails_enabled
from .tracking import _shipments, get_or_create_tracking_link, tracking_url


def build_customer_updates_context(request, order, *, can_operate, items_count=None):
    try:
        link = get_or_create_tracking_link(order)
        url = tracking_url(link, request=request)
        shipments = _shipments(order)
        drafts, suggested = build_drafts(
            order,
            tracking_url=url,
            shipments=shipments,
            items_count=items_count,
        )
        candidates = phone_candidates(order)
        recipient = resolve_recipient(order)
        recent_whatsapp = list(
            WhatsAppDraftLog.objects.filter(company_id=order.company_id, order_id=order.pk)
            .select_related("created_by")
            .only(
                "draft_key",
                "phone",
                "created_at",
                "created_by__first_name",
                "created_by__last_name",
                "created_by__email",
            )
            .order_by("-created_at")[:5]
        )
        recent_emails = list(
            CustomerEmailNotification.objects.filter(
                company_id=order.company_id, order_id=order.pk
            )
            .only("event_type", "status", "recipient", "created_at", "sent_at")
            .order_by("-created_at")[:5]
        )
    except Exception as error:
        # El panel nunca debe tirar el detalle del pedido.
        sentry_sdk.capture_exception(error)
        return {"customer_updates": None}

    draft_labels = {draft["key"]: draft["label"] for draft in drafts}
    for log in recent_whatsapp:
        log.label = draft_labels.get(log.draft_key, log.draft_key)

    return {
        "customer_updates": {
            "tracking_url": url,
            "tracking_views": link.view_count,
            "tracking_last_viewed_at": link.last_viewed_at,
            "drafts": drafts,
            "suggested_key": suggested,
            "phone_candidates": candidates,
            "recipient_email": recipient,
            "emails_enabled": customer_emails_enabled(),
            "recent_whatsapp": recent_whatsapp,
            "recent_emails": recent_emails,
            "can_operate": can_operate,
        }
    }
