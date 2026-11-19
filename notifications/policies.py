from dataclasses import dataclass

from accounts.warehouse_access import (
    ACCESS_MANAGE,
    ACCESS_OPERATE,
    ACCESS_VIEW,
)


@dataclass(frozen=True)
class NotificationPolicy:
    activity_code: str
    required_capabilities: tuple[str, ...]
    minimum_level: str
    warehouse_scoped: bool = True


EVENT_NOTIFICATION_POLICIES = {
    "staging_overdue": NotificationPolicy(
        activity_code="inventory",
        required_capabilities=("inventory.manage",),
        minimum_level=ACCESS_MANAGE,
        warehouse_scoped=True,
    ),
    "low_stock": NotificationPolicy(
        activity_code="purchasing",
        required_capabilities=("purchases.create",),
        minimum_level=ACCESS_OPERATE,
        warehouse_scoped=True,
    ),
    "out_of_stock": NotificationPolicy(
        activity_code="purchasing",
        required_capabilities=("purchases.create",),
        minimum_level=ACCESS_OPERATE,
        warehouse_scoped=True,
    ),
    "purchase_order_overdue": NotificationPolicy(
        activity_code="purchasing",
        required_capabilities=("purchases.view",),
        minimum_level=ACCESS_VIEW,
        warehouse_scoped=True,
    ),
    "shipment_batch_unconfirmed": NotificationPolicy(
        activity_code="shipping",
        required_capabilities=("shipping.execute",),
        minimum_level=ACCESS_OPERATE,
        warehouse_scoped=True,
    ),
    "shipping_incident_unresolved": NotificationPolicy(
        activity_code="shipping",
        required_capabilities=("shipping.view",),
        minimum_level=ACCESS_VIEW,
        warehouse_scoped=True,
    ),
    "shipping_field_expense_pending": NotificationPolicy(
        activity_code="collections",
        required_capabilities=("payments.view",),
        minimum_level=ACCESS_VIEW,
        warehouse_scoped=False,
    ),
}


def get_notification_policy(event_type):
    return EVENT_NOTIFICATION_POLICIES.get(
        str(event_type or "").strip()
    )
