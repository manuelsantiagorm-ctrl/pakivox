"""
Registra en django_celery_beat las tareas de mantenimiento periodico:

- liberar_reservas_vencidas_task   -> cada hora
- sync_lot_expiry_alerts_task      -> diario 03:00
- purge_old_whatsapp_payloads_task -> diario 04:00
- drain_renewed_whatsapp_queues_task -> cada hora
- sweep_customer_emails_task       -> cada 10 minutos

Idempotente: usa get_or_create, se puede correr varias veces sin
duplicar tareas ni horarios.

Uso:
    python manage.py setup_periodic_tasks
"""

from django.core.management.base import BaseCommand
from django_celery_beat.models import CrontabSchedule, PeriodicTask


class Command(BaseCommand):
    help = "Registra las tareas periodicas de mantenimiento en Celery Beat."

    def handle(self, *args, **options):
        every_hour, _ = CrontabSchedule.objects.get_or_create(
            minute="0", hour="*", day_of_week="*", day_of_month="*", month_of_year="*"
        )
        daily_3am, _ = CrontabSchedule.objects.get_or_create(
            minute="0", hour="3", day_of_week="*", day_of_month="*", month_of_year="*"
        )
        daily_4am, _ = CrontabSchedule.objects.get_or_create(
            minute="0", hour="4", day_of_week="*", day_of_month="*", month_of_year="*"
        )
        every_10_minutes, _ = CrontabSchedule.objects.get_or_create(
            minute="*/10", hour="*", day_of_week="*", day_of_month="*", month_of_year="*"
        )

        tasks = [
            (
                "Liberar reservas vencidas (cada hora)",
                "warehouse.tasks.liberar_reservas_vencidas_task",
                every_hour,
            ),
            (
                "Sincronizar alertas de caducidad de lotes (diario 03:00)",
                "inventory.tasks.sync_lot_expiry_alerts_task",
                daily_3am,
            ),
            (
                "Purgar payloads WhatsApp - BUG-067 (diario 04:00)",
                "messaging.tasks.purge_old_whatsapp_payloads_task",
                daily_4am,
            ),
            (
                "Reenviar WhatsApp detenidos tras renovacion - PLANES 2.4b (cada hora)",
                "subscriptions.tasks.drain_renewed_whatsapp_queues_task",
                every_hour,
            ),
            (
                "Reintentar correos a clientes pendientes (cada 10 minutos)",
                "customer_updates.tasks.sweep_customer_emails_task",
                every_10_minutes,
            ),
        ]
        for name, task_path, schedule in tasks:
            obj, created = PeriodicTask.objects.get_or_create(
                name=name,
                defaults={
                    "task": task_path,
                    "crontab": schedule,
                    "enabled": True,
                },
            )
            if not created:
                obj.task = task_path
                obj.crontab = schedule
                obj.enabled = True
                obj.interval = None
                obj.save()
                estado = "actualizada"
            else:
                estado = "creada"

            self.stdout.write(self.style.SUCCESS(f"  {estado}: {name}"))

        self.stdout.write(self.style.SUCCESS("Listo. Verifica en Django Admin -> Periodic Tasks."))
