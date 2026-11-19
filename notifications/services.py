from datetime import timedelta

from django.db import transaction
from django.db.models import F, Min, Sum
from django.utils import timezone

from .models import Notification


@transaction.atomic
@transaction.atomic
def sync_stock_notification(alert):
    dedupe_key = (
        f"inventory.stock_critical:"
        f"{alert.warehouse_id}:{alert.variant_id}"
    )
    active = (
        Notification.objects.select_for_update()
        .filter(
            company_id=alert.company_id,
            dedupe_key=dedupe_key,
            status=Notification.STATUS_ACTIVE,
        )
        .first()
    )

    if alert.status != alert.STATUS_ACTIVE:
        if active:
            active.status = Notification.STATUS_RESOLVED
            active.resolved_at = timezone.now()
            active.save(
                update_fields=["status", "resolved_at", "updated_at"]
            )
        return None

    exhausted = alert.alert_type == alert.TYPE_OUT_OF_STOCK
    values = {
        "warehouse_id": alert.warehouse_id,
        "category": "inventory",
        "event_type": alert.alert_type,
        "level": (
            Notification.LEVEL_CRITICAL
            if exhausted
            else Notification.LEVEL_WARNING
        ),
        "title": "Stock agotado" if exhausted else "Stock bajo",
        "message": (
            f"{alert.variant.sku} tiene {alert.current_available} "
            f"pieza(s) disponibles. Mínimo configurado: "
            f"{alert.min_stock}. Bodega: {alert.warehouse.name}."
        ),
        "icon": (
            "bi-x-octagon-fill"
            if exhausted
            else "bi-exclamation-triangle-fill"
        ),
        "action_label": "Revisar reabastecimiento",
        "action_name": "purchases:replenishment_alert_detail",
        "action_kwargs": {"alert_id": alert.pk},
        "source_app": "inventory",
        "source_model": "StockCriticalAlert",
        "source_object_id": alert.pk,
        "reference": alert.reference or "",
        "resolved_at": None,
    }

    if active:
        changed_level = active.level != values["level"]
        for field, value in values.items():
            setattr(active, field, value)
        active.save(update_fields=[*values.keys(), "updated_at"])
        if changed_level:
            active.user_states.all().delete()
        return active

    return Notification.objects.create(
        company_id=alert.company_id,
        dedupe_key=dedupe_key,
        **values,
    )


def _upsert_active_notification(*, company_id, dedupe_key, values):
    active = (
        Notification.objects.select_for_update()
        .filter(
            company_id=company_id,
            dedupe_key=dedupe_key,
            status=Notification.STATUS_ACTIVE,
        )
        .first()
    )

    if active:
        changed_level = active.level != values["level"]
        changed_content = any(
            getattr(active, field) != value
            for field, value in values.items()
        )
        if changed_content:
            for field, value in values.items():
                setattr(active, field, value)
            active.save(update_fields=[*values.keys(), "updated_at"])
        if changed_level:
            active.user_states.all().delete()
        return active, False

    return (
        Notification.objects.create(
            company_id=company_id,
            dedupe_key=dedupe_key,
            **values,
        ),
        True,
    )


def _resolve_missing_notifications(*, event_type, active_keys):
    now = timezone.now()
    queryset = Notification.objects.filter(
        event_type=event_type,
        status=Notification.STATUS_ACTIVE,
    )
    if active_keys:
        queryset = queryset.exclude(dedupe_key__in=active_keys)
    return queryset.update(
        status=Notification.STATUS_RESOLVED,
        resolved_at=now,
        updated_at=now,
    )


@transaction.atomic
def sync_staging_notifications(hours=24):
    """
    Crea una alerta por empresa+bodega cuando todavía existe mercancía
    física en STAGING y su tarea abierta más antigua superó el límite.
    """
    from warehouse.models import PutawayTask, VariantLocation, WarehouseLocation

    cutoff = timezone.now() - timedelta(hours=hours)
    open_statuses = [
        PutawayTask.STATUS_PENDING,
        PutawayTask.STATUS_IN_PROGRESS,
    ]
    overdue_groups = (
        PutawayTask.objects.filter(
            status__in=open_statuses,
            quantity__gt=0,
            created_at__lte=cutoff,
            company_id=F("warehouse__company_id"),
        )
        .values("company_id", "warehouse_id")
        .annotate(
            oldest_at=Min("created_at"),
            task_quantity=Sum("quantity"),
        )
    )

    active_keys = set()
    created = 0
    updated = 0

    for group in overdue_groups.iterator():
        staging_quantity = (
            VariantLocation.objects.filter(
                company_id=group["company_id"],
                location__company_id=group["company_id"],
                location__warehouse_id=group["warehouse_id"],
                location__status=WarehouseLocation.STATUS_STAGING,
                location__is_virtual=True,
                quantity__gt=0,
            ).aggregate(total=Sum("quantity"))["total"]
            or 0
        )
        if staging_quantity <= 0:
            continue

        oldest_at = group["oldest_at"]
        elapsed_hours = max(
            24,
            int((timezone.now() - oldest_at).total_seconds() // 3600),
        )
        warehouse = (
            PutawayTask.objects.filter(
                company_id=group["company_id"],
                warehouse_id=group["warehouse_id"],
            )
            .select_related("warehouse")
            .values("warehouse__name")
            .first()
        )
        warehouse_name = warehouse["warehouse__name"]
        dedupe_key = f"warehouse.staging_overdue:{group['warehouse_id']}"
        active_keys.add(dedupe_key)
        _, was_created = _upsert_active_notification(
            company_id=group["company_id"],
            dedupe_key=dedupe_key,
            values={
                "warehouse_id": group["warehouse_id"],
                "category": "inventory",
                "event_type": "staging_overdue",
                "level": Notification.LEVEL_WARNING,
                "status": Notification.STATUS_ACTIVE,
                "title": "Mercancía pendiente de ubicar",
                "message": (
                    f"{staging_quantity} pieza(s) llevan al menos "
                    f"{elapsed_hours} h en STAGING. Bodega: "
                    f"{warehouse_name}."
                ),
                "icon": "bi-box-seam-fill",
                "action_label": "Ubicar mercancía",
                "action_name": "warehouse:ubicar_mercancia",
                "action_kwargs": {},
                "source_app": "warehouse",
                "source_model": "Warehouse",
                "source_object_id": group["warehouse_id"],
                "reference": warehouse_name,
                "resolved_at": None,
            },
        )
        created += int(was_created)
        updated += int(not was_created)

    resolved = _resolve_missing_notifications(
        event_type="staging_overdue",
        active_keys=active_keys,
    )
    return {"created": created, "updated": updated, "resolved": resolved}


@transaction.atomic
def sync_overdue_purchase_order_notifications():
    from purchases.models import PurchaseOrder

    open_statuses = [
        PurchaseOrder.STATUS_SENT,
        PurchaseOrder.STATUS_PARTIALLY_RECEIVED,
    ]
    overdue_orders = PurchaseOrder.objects.select_related(
        "company",
        "warehouse",
        "supplier",
    ).filter(
        expected_date__lt=timezone.localdate(),
        status__in=open_statuses,
        warehouse__company_id=F("company_id"),
        supplier__company_id=F("company_id"),
    )

    active_keys = set()
    created = 0
    updated = 0

    for purchase_order in overdue_orders.iterator():
        days_overdue = (timezone.localdate() - purchase_order.expected_date).days
        dedupe_key = f"purchases.order_overdue:{purchase_order.pk}"
        active_keys.add(dedupe_key)
        _, was_created = _upsert_active_notification(
            company_id=purchase_order.company_id,
            dedupe_key=dedupe_key,
            values={
                "warehouse_id": purchase_order.warehouse_id,
                "category": "purchases",
                "event_type": "purchase_order_overdue",
                "level": Notification.LEVEL_WARNING,
                "status": Notification.STATUS_ACTIVE,
                "title": "Orden de compra atrasada",
                "message": (
                    f"{purchase_order.folio} lleva {days_overdue} día(s) "
                    f"de atraso. Proveedor: {purchase_order.supplier.name}. "
                    f"Bodega: {purchase_order.warehouse.name}."
                ),
                "icon": "bi-cart-x-fill",
                "action_label": "Ver orden",
                "action_name": "purchases:detalle_orden_compra",
                "action_kwargs": {
                    "purchase_order_id": purchase_order.pk,
                },
                "source_app": "purchases",
                "source_model": "PurchaseOrder",
                "source_object_id": purchase_order.pk,
                "reference": purchase_order.folio,
                "resolved_at": None,
            },
        )
        created += int(was_created)
        updated += int(not was_created)

    resolved = _resolve_missing_notifications(
        event_type="purchase_order_overdue",
        active_keys=active_keys,
    )
    return {"created": created, "updated": updated, "resolved": resolved}


@transaction.atomic
def sync_shipment_batch_notification(batch):
    """
    Notificación escalonada para un lote sin confirmar.

    12h -> info | 24h -> warning | 48h -> critical.
    Se resuelve automáticamente en cuanto el lote deja de estar 'open'.
    """
    from shipping_hub.models import ShipmentBatch

    dedupe_key = f"shipping.batch_unconfirmed:{batch.pk}"

    if batch.status != ShipmentBatch.STATUS_OPEN:
        active = (
            Notification.objects.select_for_update()
            .filter(
                company_id=batch.company_id,
                dedupe_key=dedupe_key,
                status=Notification.STATUS_ACTIVE,
            )
            .first()
        )
        if active:
            active.status = Notification.STATUS_RESOLVED
            active.resolved_at = timezone.now()
            active.save(update_fields=["status", "resolved_at", "updated_at"])
        return None

    hours_open = (timezone.now() - batch.created_at).total_seconds() / 3600

    if hours_open >= 48:
        level = Notification.LEVEL_CRITICAL
        title = "Lote sin confirmar por más de 48 horas"
    elif hours_open >= 24:
        level = Notification.LEVEL_WARNING
        title = "Lote sin confirmar por más de 24 horas"
    elif hours_open >= 12:
        level = Notification.LEVEL_INFO
        title = "Lote sin confirmar por más de 12 horas"
    else:
        return None  # aún no llega al primer umbral

    warehouse_id = batch.collection_point.warehouse_id
    assignee = (
        batch.assigned_to.get_username() if batch.assigned_to_id else "sin asignar"
    )

    return _upsert_active_notification(
        company_id=batch.company_id,
        dedupe_key=dedupe_key,
        values={
            "warehouse_id": warehouse_id,
            "category": "shipping",
            "event_type": "shipment_batch_unconfirmed",
            "level": level,
            "status": Notification.STATUS_ACTIVE,
            "title": title,
            "message": (
                f"Lote #{batch.pk} en '{batch.collection_point.name}' sigue sin "
                f"confirmarse. Asignado a: {assignee}."
            ),
            "icon": "bi-box-seam-fill",
            "action_label": "Ver lote",
            "action_name": "shipping_hub:shipment_batch_scan",
            "action_kwargs": {"qr_token": str(batch.collection_point.qr_token)},
            "source_app": "shipping_hub",
            "source_model": "ShipmentBatch",
            "source_object_id": batch.pk,
            "reference": f"Lote #{batch.pk}",
            "resolved_at": None,
        },
    )


@transaction.atomic
def sync_shipping_incident_notification(incident):
    """
    Notificación escalonada para una incidencia (faltante o rechazo) sin resolver.

    Inmediato -> info | 24h -> warning | 48h -> critical.
    Se resuelve automáticamente en cuanto el incidente deja de estar abierto.
    """
    from shipping_hub.models import LogisticsIncident

    dedupe_key = f"shipping.incident_unresolved:{incident.pk}"

    if incident.status not in (
        LogisticsIncident.STATUS_OPEN,
        LogisticsIncident.STATUS_INVESTIGATING,
    ):
        active = (
            Notification.objects.select_for_update()
            .filter(
                company_id=incident.company_id,
                dedupe_key=dedupe_key,
                status=Notification.STATUS_ACTIVE,
            )
            .first()
        )
        if active:
            active.status = Notification.STATUS_RESOLVED
            active.resolved_at = timezone.now()
            active.save(update_fields=["status", "resolved_at", "updated_at"])
        return None

    hours_open = (timezone.now() - incident.opened_at).total_seconds() / 3600

    if hours_open >= 48:
        level = Notification.LEVEL_CRITICAL
        title = "Incidencia sin resolver por más de 48 horas"
    elif hours_open >= 24:
        level = Notification.LEVEL_WARNING
        title = "Incidencia sin resolver por más de 24 horas"
    else:
        level = Notification.LEVEL_INFO
        title = "Nueva incidencia logística"

    return _upsert_active_notification(
        company_id=incident.company_id,
        dedupe_key=dedupe_key,
        values={
            "warehouse_id": incident.warehouse_id,
            "category": "shipping",
            "event_type": "shipping_incident_unresolved",
            "level": level,
            "status": Notification.STATUS_ACTIVE,
            "title": title,
            "message": incident.title,
            "icon": "bi-exclamation-triangle-fill",
            "action_label": "Ver incidencia",
            "action_name": "shipping_hub:incident_detail",
            "action_kwargs": {"incident_id": incident.pk},
            "source_app": "shipping_hub",
            "source_model": "LogisticsIncident",
            "source_object_id": incident.pk,
            "reference": incident.title,
            "resolved_at": None,
        },
    )


@transaction.atomic
def notify_field_expense_pending(receipt):
    """
    Notificación específica y separada para el equipo de finanzas cuando
    un recolector registra un gasto imprevisto en sitio (guía pagada
    directamente en el mostrador de la paquetería). No es la misma
    notificación que la incidencia general -- esta es explícita sobre
    el gasto y pide que se coordinen con quien lo reportó.
    """
    dedupe_key = f"shipping.field_expense:{receipt.pk}"

    reporter = (
        receipt.reported_by.get_username()
        if receipt.reported_by_id
        else "un recolector"
    )

    return _upsert_active_notification(
        company_id=receipt.company_id,
        dedupe_key=dedupe_key,
        values={
            "warehouse_id": None,
            "category": "finance",
            "event_type": "shipping_field_expense_pending",
            "level": Notification.LEVEL_WARNING,
            "status": Notification.STATUS_ACTIVE,
            "title": "Gasto imprevisto pendiente de conciliar",
            "message": (
                f"{reporter} pagó ${receipt.amount} {receipt.currency} en sitio "
                f"por una guía de {receipt.carrier_name or 'paquetería'}. "
                "Coordínate con él/ella para el reembolso o comprobación, "
                "y concilia el gasto en cuanto puedas."
            ),
            "icon": "bi-cash-stack",
            "action_label": "Ver comprobante",
            "action_name": "shipping_hub:incident_detail",
            "action_kwargs": {"incident_id": receipt.incident_id},
            "source_app": "shipping_hub",
            "source_model": "IncidentExpenseReceipt",
            "source_object_id": receipt.pk,
            "reference": f"${receipt.amount} {receipt.currency}",
            "resolved_at": None,
        },
    )
