from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("sales", "0007_cs1b_recovery_events")]

    operations = [
        migrations.AlterField(
            model_name="orderresolutionevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("cancel_requested", "Cancelación solicitada"),
                    ("cancelled", "Pedido cancelado"),
                    ("resolution_required", "Requiere resolución segura"),
                    ("recovery_started", "Recuperación iniciada"),
                    ("recovered", "Pedido recuperado"),
                    ("recovery_blocked", "Recuperación bloqueada"),
                    ("commercial_cancel_requested", "Cancelación comercial solicitada"),
                    ("commercial_cancelled", "Cancelación comercial completada"),
                    ("refunds_requested", "Reembolsos solicitados"),
                    ("commercial_cancel_blocked", "Cancelación comercial bloqueada"),
                    ("blocked", "Acción bloqueada"),
                ],
                db_index=True,
                max_length=40,
            ),
        ),
    ]
