from decimal import Decimal

from django.db import migrations

TRIAL_PLAN_SLUG = "trial-14-dias"


def create_trial_plan(apps, schema_editor):
    Plan = apps.get_model("subscriptions", "Plan")

    Plan.objects.update_or_create(
        slug=TRIAL_PLAN_SLUG,
        defaults={
            "name": "Plan Pro - Prueba de 14 días",
            "description": (
                "Prueba gratuita de WMS360 durante 14 días. "
                "Incluye una bodega, cuatro usuarios totales, "
                "cien productos y veinte consultas al "
                "Copiloto Ejecutivo."
            ),
            "price": Decimal("0.00"),
            "billing_period": "monthly",
            "max_users": 4,
            "max_products": 100,
            "max_warehouses": 1,
            "has_whatsapp": False,
            "max_whatsapp_numbers": 0,
            "max_whatsapp_agents": 0,
            "can_create_orders_from_chat": False,
            "can_supervise_whatsapp": False,
            "has_ai": True,
            "max_ai_queries": 20,
            "has_api": False,
            "has_multiwarehouse": False,
            "is_active": True,
        },
    )


def preserve_trial_plan(apps, schema_editor):
    """
    No elimina el plan al revertir la migración.

    Una suscripción podría estar relacionada con este plan.
    Eliminarlo durante un rollback podría provocar pérdida
    o corrupción de relaciones comerciales.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        (
            "subscriptions",
            "0003_aiqueryusage_stripewebhookevent_and_more",
        ),
    ]

    operations = [
        migrations.RunPython(
            create_trial_plan,
            preserve_trial_plan,
        ),
    ]
