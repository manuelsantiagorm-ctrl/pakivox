from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("messaging", "0015_ycloud_assisted_onboarding"),
    ]
    operations = [
        migrations.CreateModel(
            name="OnboardingStatusEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("previous_status", models.CharField(max_length=30)),
                ("new_status", models.CharField(max_length=30)),
                ("public_note", models.TextField(blank=True, default="")),
                ("internal_note", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="whatsapp_onboarding_events", to="companies.company")),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="whatsapp_onboarding_events_created", to=settings.AUTH_USER_MODEL)),
                ("warehouse", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="whatsapp_onboarding_events", to="inventory.warehouse")),
                ("whatsapp_number", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="onboarding_events", to="messaging.whatsappnumber")),
            ],
            options={"ordering": ["-created_at", "-pk"]},
        ),
        migrations.AddIndex(model_name="onboardingstatusevent", index=models.Index(fields=["company", "created_at"], name="messaging_o_company_3f52f0_idx")),
        migrations.AddIndex(model_name="onboardingstatusevent", index=models.Index(fields=["whatsapp_number", "created_at"], name="messaging_o_whatsap_5b429c_idx")),
        migrations.AddIndex(model_name="onboardingstatusevent", index=models.Index(fields=["new_status", "created_at"], name="messaging_o_new_sta_a7bc1c_idx")),
    ]
