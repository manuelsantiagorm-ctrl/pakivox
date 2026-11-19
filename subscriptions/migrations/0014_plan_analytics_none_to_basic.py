from django.db import migrations


def none_to_basic(apps, schema_editor):
    """El nivel "sin acceso" desaparece: quien lo tenia pasa a "basico"."""
    Plan = apps.get_model("subscriptions", "Plan")
    Plan.objects.filter(analytics_level="none").update(analytics_level="basic")


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0013_plan_analytics_levels"),
    ]

    operations = [
        migrations.RunPython(none_to_basic, migrations.RunPython.noop),
    ]
