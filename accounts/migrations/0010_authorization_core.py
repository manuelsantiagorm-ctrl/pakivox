from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def preserve_existing_admin_access(apps, schema_editor):
    Profile = apps.get_model("accounts", "UserProfile")
    Capability = apps.get_model("accounts", "Capability")
    Assignment = apps.get_model("accounts", "EmployeeCapabilityAssignment")
    WarehouseAccess = apps.get_model("accounts", "UserWarehouseAccess")
    Warehouse = apps.get_model("inventory", "Warehouse")
    capability_ids = list(
        Capability.objects.filter(is_active=True).values_list("id", flat=True)
    )
    for profile in Profile.objects.filter(
        role="admin", is_active=True, employment_status="active"
    ).iterator():
        for capability_id in capability_ids:
            Assignment.objects.update_or_create(
                user_id=profile.user_id,
                capability_id=capability_id,
                defaults={
                    "company_id": profile.company_id,
                    "is_active": True,
                },
            )
        for warehouse_id in Warehouse.objects.filter(
            company_id=profile.company_id
        ).values_list("id", flat=True):
            WarehouseAccess.objects.update_or_create(
                user_id=profile.user_id,
                warehouse_id=warehouse_id,
                defaults={
                    "company_id": profile.company_id,
                    "access_level": "manage",
                    "is_active": True,
                },
            )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0009_employee_multirole")]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="access_version",
            field=models.PositiveBigIntegerField(
                default=1,
                help_text="Se incrementa al revocar o modificar permisos sensibles.",
                verbose_name="Versión de acceso",
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="employment_status",
            field=models.CharField(
                choices=[
                    ("active", "Activo"),
                    ("suspended", "Suspendido temporalmente"),
                    ("blocked", "Bloqueado por seguridad"),
                    ("disabled", "Desactivado"),
                    ("terminated", "Baja laboral"),
                ],
                db_index=True,
                default="active",
                max_length=20,
                verbose_name="Estado laboral",
            ),
        ),
        migrations.AlterField(
            model_name="userwarehouseaccess",
            name="access_level",
            field=models.CharField(
                choices=[
                    ("view", "Solo consulta"),
                    ("operate", "Operación"),
                    ("approve", "Aprobación"),
                    ("manage", "Administración"),
                ],
                default="operate",
                max_length=20,
                verbose_name="Nivel de acceso",
            ),
        ),
        migrations.AlterField(
            model_name="invitationwarehouseaccess",
            name="access_level",
            field=models.CharField(
                choices=[
                    ("view", "Solo consulta"),
                    ("operate", "Operación"),
                    ("approve", "Aprobación"),
                    ("manage", "Administración"),
                ],
                default="operate",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="employeeaccessaudit",
            name="ip_address",
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="employeeaccessaudit",
            name="permission_code",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="employeeaccessaudit",
            name="reason",
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.AddField(
            model_name="employeeaccessaudit",
            name="request_method",
            field=models.CharField(blank=True, max_length=10),
        ),
        migrations.AddField(
            model_name="employeeaccessaudit",
            name="request_path",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="employeeaccessaudit",
            name="result",
            field=models.CharField(default="success", max_length=20),
        ),
        migrations.CreateModel(
            name="EmployeeCapabilityOverride",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "effect",
                    models.CharField(
                        choices=[
                            ("allow", "Permitir excepcionalmente"),
                            ("deny", "Denegar explícitamente"),
                        ],
                        max_length=10,
                    ),
                ),
                ("reason", models.CharField(max_length=300)),
                ("starts_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "assigned_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "capability",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="employee_overrides",
                        to="accounts.capability",
                    ),
                ),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="employee_capability_overrides",
                        to="companies.company",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="capability_overrides",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="employeecapabilityoverride",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_active", True)),
                fields=("user", "capability"),
                name="unique_active_employee_cap_override",
            ),
        ),
        migrations.AddIndex(
            model_name="employeecapabilityoverride",
            index=models.Index(
                fields=["company", "user", "is_active"],
                name="emp_cap_override_idx",
            ),
        ),
        migrations.RunPython(
            preserve_existing_admin_access,
            migrations.RunPython.noop,
        ),
    ]
