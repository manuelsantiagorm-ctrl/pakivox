from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0006_order_resolution_event"),
    ]

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
                    ("blocked", "Acción bloqueada"),
                ],
                db_index=True,
                max_length=40,
            ),
        ),
    ]
