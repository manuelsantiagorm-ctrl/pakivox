from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("accounts", "0022_owneractionaudit")]

    operations = [
        migrations.AddField(
            model_name="pendingcompanyregistration",
            name="verification_attempt_count",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="Intentos de código"),
        ),
        migrations.AddConstraint(
            model_name="pendingcompanyregistration",
            constraint=models.UniqueConstraint(
                fields=("email",),
                condition=Q(status="pending"),
                name="uniq_pending_registration_email",
            ),
        ),
    ]
