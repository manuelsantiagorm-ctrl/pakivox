from django.core.management.base import BaseCommand

from inventory.models import StockCriticalAlert
from notifications.services import (
    sync_overdue_purchase_order_notifications,
    sync_staging_notifications,
    sync_stock_notification,
)


class Command(BaseCommand):
    help = "Sincroniza alertas existentes con el centro de notificaciones."

    def handle(self, *args, **options):
        alerts = StockCriticalAlert.objects.select_related(
            "company",
            "warehouse",
            "variant",
        )
        synchronized = 0

        for alert in alerts.iterator():
            sync_stock_notification(alert)
            synchronized += 1

        staging = sync_staging_notifications(hours=24)
        purchases = sync_overdue_purchase_order_notifications()

        self.stdout.write(
            self.style.SUCCESS(
                "Notificaciones sincronizadas. "
                f"Stock: {synchronized}. "
                f"STAGING: {staging}. "
                f"Compras: {purchases}."
            )
        )
