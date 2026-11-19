from django.db import transaction
from django.utils import timezone

from inventory.models import Stock, StockCriticalAlert
from notifications.services import sync_stock_notification


def get_stock_alert_type(available):
    """
    Determina el tipo de alerta según el inventario disponible.
    """
    if available <= 0:
        return StockCriticalAlert.TYPE_OUT_OF_STOCK

    return StockCriticalAlert.TYPE_LOW_STOCK


def build_stock_critical_message(stock, min_stock, alert_type):
    """
    Construye el mensaje mostrado en la alerta de inventario crítico.
    """
    variant = stock.variant
    product = getattr(variant, "product", None)

    product_name = product.name if product else str(variant)
    sku = variant.sku
    warehouse_name = (
        stock.warehouse.name if stock.warehouse_id else "Bodega no registrada"
    )

    available = stock.available_stock

    if alert_type == StockCriticalAlert.TYPE_OUT_OF_STOCK:
        title = "🚨 ALERTA DE PRODUCTO AGOTADO (WMS360)"
        situation = "se quedó sin existencias"
        recommendation = (
            "Se requiere reabastecimiento inmediato o generar " "una orden de compra."
        )
    else:
        title = "⚠️ ALERTA DE STOCK BAJO (WMS360)"
        situation = "está por agotarse"
        recommendation = "Se sugiere generar una orden de compra."

    if product and getattr(product, "stock_mode", "unit") == "bulk":
        try:
            commercial_available = product.to_commercial_quantity(available)

            commercial_label = product.get_commercial_unit_display()
            base_label = product.get_base_unit_display()

            return (
                f"{title}\n\n"
                f"El producto {product_name} [{sku}] "
                f"en {warehouse_name} {situation}.\n\n"
                f"Existencias actuales: "
                f"{commercial_available:.2f} {commercial_label} "
                f"({available} {base_label} internas).\n"
                f"Stock mínimo configurado: "
                f"{min_stock} {base_label} internas.\n\n"
                f"{recommendation}"
            )
        except (AttributeError, TypeError, ValueError):
            pass

    return (
        f"{title}\n\n"
        f"El producto {product_name} [{sku}] "
        f"en {warehouse_name} {situation}.\n\n"
        f"Existencias actuales: {available} unidades.\n"
        f"Stock mínimo configurado: {min_stock} unidades.\n\n"
        f"{recommendation}"
    )


@transaction.atomic
def check_stock_critical(stock, source="manual", reference=""):
    """
    Evalúa un registro Stock específico por empresa, bodega y variante.

    Reglas:
    - Mantiene una sola alerta activa por empresa, bodega y variante.
    - Disponible entre 1 y min_stock: stock bajo.
    - Disponible igual o menor que 0: producto agotado.
    - Disponible mayor que min_stock: resuelve las alertas activas.
    - min_stock igual a 0: desactiva y resuelve las alertas activas.
    """

    if stock is None or not getattr(stock, "pk", None):
        return None

    locked_stock = (
        Stock.objects.select_for_update()
        .select_related(
            "company",
            "warehouse",
            "variant",
            "variant__product",
        )
        .filter(
            pk=stock.pk,
            company_id=stock.company_id,
            warehouse_id=stock.warehouse_id,
            variant_id=stock.variant_id,
        )
        .first()
    )

    if locked_stock is None:
        return None

    min_stock = int(getattr(locked_stock.variant, "min_stock", 0) or 0)
    available = int(locked_stock.available_stock)

    active_alerts = (
        StockCriticalAlert.objects.select_for_update()
        .filter(
            company_id=locked_stock.company_id,
            warehouse_id=locked_stock.warehouse_id,
            variant_id=locked_stock.variant_id,
            status=StockCriticalAlert.STATUS_ACTIVE,
        )
        .order_by("-created_at", "-pk")
    )

    active_alert = active_alerts.first()

    if min_stock <= 0 or available > min_stock:
        now = timezone.now()
        alerts_to_resolve = list(active_alerts)

        if not alerts_to_resolve:
            return None

        alert_ids = [alert.pk for alert in alerts_to_resolve]

        StockCriticalAlert.objects.filter(
            pk__in=alert_ids,
            company_id=locked_stock.company_id,
            warehouse_id=locked_stock.warehouse_id,
            variant_id=locked_stock.variant_id,
        ).update(
            stock=locked_stock,
            current_available=available,
            min_stock=max(min_stock, 0),
            status=StockCriticalAlert.STATUS_RESOLVED,
            is_read=True,
            resolved_at=now,
            updated_at=now,
        )

        resolved_alerts = StockCriticalAlert.objects.filter(
            pk__in=alert_ids,
            company_id=locked_stock.company_id,
            warehouse_id=locked_stock.warehouse_id,
            variant_id=locked_stock.variant_id,
        )

        for resolved_alert in resolved_alerts:
            sync_stock_notification(resolved_alert)

        return None

    alert_type = get_stock_alert_type(available)

    message = build_stock_critical_message(
        stock=locked_stock,
        min_stock=min_stock,
        alert_type=alert_type,
    )

    source = str(source or "manual").strip()[:80]
    reference = str(reference or "").strip()[:150]

    if active_alert is not None:
        changed_type = active_alert.alert_type != alert_type

        active_alert.stock = locked_stock
        active_alert.alert_type = alert_type
        active_alert.status = StockCriticalAlert.STATUS_ACTIVE
        active_alert.current_available = available
        active_alert.min_stock = min_stock
        active_alert.source = source
        active_alert.reference = reference
        active_alert.message = message
        active_alert.resolved_at = None

        if changed_type:
            active_alert.is_read = False

        active_alert.save(
            update_fields=[
                "stock",
                "alert_type",
                "status",
                "current_available",
                "min_stock",
                "source",
                "reference",
                "message",
                "is_read",
                "resolved_at",
                "updated_at",
            ]
        )

        sync_stock_notification(active_alert)
        return active_alert

    active_alert = StockCriticalAlert.objects.create(
        company=locked_stock.company,
        warehouse=locked_stock.warehouse,
        variant=locked_stock.variant,
        stock=locked_stock,
        alert_type=alert_type,
        status=StockCriticalAlert.STATUS_ACTIVE,
        current_available=available,
        min_stock=min_stock,
        source=source,
        reference=reference,
        message=message,
        is_read=False,
    )

    sync_stock_notification(active_alert)
    return active_alert
