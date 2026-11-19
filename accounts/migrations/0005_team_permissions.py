# Generated for WMS360 Fase 1.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


CAPABILITIES = [
    ("dashboard.view", "Consultar dashboard", "direction"),
    ("team.manage", "Administrar equipo", "direction"),
    ("inventory.view", "Consultar inventario", "warehouse"),
    ("inventory.manage", "Administrar inventario", "warehouse"),
    ("picking.execute", "Surtir pedidos", "warehouse"),
    ("packing.execute", "Empacar pedidos", "packing"),
    ("quality.approve", "Aprobar control de calidad", "packing"),
    ("shipping.execute", "Despachar pedidos", "packing"),
    ("sales.view", "Consultar pedidos", "sales"),
    ("sales.create", "Crear pedidos", "sales"),
    ("crm.manage", "Gestionar clientes y oportunidades", "sales"),
    ("messaging.operate", "Atender WhatsApp", "sales"),
    ("payments.view", "Consultar pagos", "finance"),
    ("payments.approve", "Aprobar o rechazar pagos", "finance"),
    ("purchases.manage", "Gestionar compras", "purchases"),
    ("receiving.execute", "Recibir mercancía", "purchases"),
    ("accounting.view", "Consultar contabilidad", "finance"),
    ("imports.manage", "Ejecutar importaciones", "direction"),
]

AREAS = [
    ("direction", "Dirección y control", 10),
    ("sales", "Ventas, CRM y atención", 20),
    ("finance", "Caja y finanzas", 30),
    ("purchases", "Compras y recepción", 40),
    ("warehouse", "Operaciones de almacén", 50),
    ("packing", "Packing, calidad y despacho", 60),
]

TEMPLATES = [
    ("vendedor", "Vendedor", "sales", ["sales.view", "sales.create", "crm.manage"]),
    ("cajero", "Caja y cobranza", "accounting", ["payments.view", "payments.approve"]),
    ("comprador", "Comprador", "manager", ["purchases.manage", "inventory.view"]),
    ("receptor", "Recepción", "warehouse", ["receiving.execute", "inventory.view"]),
    ("surtidor", "Surtidor", "warehouse", ["inventory.view", "picking.execute"]),
    ("empacador", "Empacador", "warehouse", ["packing.execute", "shipping.execute"]),
    ("calidad", "Control de calidad", "warehouse", ["quality.approve", "packing.execute"]),
    ("contador", "Contabilidad", "accounting", ["accounting.view", "payments.view"]),
    ("supervisor", "Supervisor de almacén", "manager", ["inventory.view", "inventory.manage", "picking.execute", "packing.execute", "quality.approve", "shipping.execute"]),
    ("administrador", "Administrador", "admin", [code for code, _, _ in CAPABILITIES]),
]


def seed_catalogs(apps, schema_editor):
    Company = apps.get_model("companies", "Company")
    Capability = apps.get_model("accounts", "Capability")
    WorkArea = apps.get_model("accounts", "WorkArea")
    JobTemplate = apps.get_model("accounts", "JobTemplate")

    capabilities = {}
    for code, name, area_code in CAPABILITIES:
        capability, _ = Capability.objects.get_or_create(
            code=code,
            defaults={"name": name, "area_code": area_code, "is_active": True},
        )
        capabilities[code] = capability

    for company in Company.objects.all().iterator():
        for code, name, sort_order in AREAS:
            WorkArea.objects.get_or_create(
                company=company,
                code=code,
                defaults={"name": name, "sort_order": sort_order, "is_active": True},
            )
        for code, name, role, capability_codes in TEMPLATES:
            template, _ = JobTemplate.objects.get_or_create(
                company=company,
                code=code,
                defaults={"name": name, "role": role, "is_system": True},
            )
            template.capabilities.set(
                [capabilities[item] for item in capability_codes]
            )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_employeeinvitation"),
        ("companies", "0003_company_media_profile"),
        ("inventory", "0008_productvariant_min_stock_stockcriticalalert"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Capability",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("code", models.CharField(max_length=100, unique=True)),
                ("name", models.CharField(max_length=140)),
                ("description", models.CharField(blank=True, max_length=240)),
                ("area_code", models.SlugField(db_index=True, max_length=50)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={"ordering": ["area_code", "name"]},
        ),
        migrations.CreateModel(
            name="WorkArea",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("code", models.SlugField(max_length=50)),
                ("name", models.CharField(max_length=100)),
                ("description", models.CharField(blank=True, max_length=240)),
                ("icon", models.CharField(blank=True, max_length=50)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="work_areas", to="companies.company")),
            ],
            options={
                "ordering": ["sort_order", "name"],
                "constraints": [models.UniqueConstraint(fields=("company", "code"), name="unique_company_work_area")],
            },
        ),
        migrations.CreateModel(
            name="JobTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("code", models.SlugField(max_length=50)),
                ("name", models.CharField(max_length=100)),
                ("role", models.CharField(choices=[("owner", "Dueño"), ("admin", "Administrador"), ("manager", "Gerente"), ("sales", "Ventas"), ("warehouse", "Bodega"), ("accounting", "Contabilidad")], max_length=30)),
                ("description", models.CharField(blank=True, max_length=240)),
                ("is_system", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(default=True)),
                ("capabilities", models.ManyToManyField(blank=True, related_name="job_templates", to="accounts.capability")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="job_templates", to="companies.company")),
            ],
            options={
                "ordering": ["name"],
                "constraints": [models.UniqueConstraint(fields=("company", "code"), name="unique_company_job_template")],
            },
        ),
        migrations.AddField(
            model_name="employeeinvitation",
            name="job_template",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="invitations", to="accounts.jobtemplate", verbose_name="Puesto"),
        ),
        migrations.CreateModel(
            name="EmployeeAreaAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("area", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="employee_assignments", to="accounts.workarea")),
                ("assigned_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="companies.company")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="work_area_assignments", to=settings.AUTH_USER_MODEL)),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("user", "area"), name="unique_employee_area")]},
        ),
        migrations.CreateModel(
            name="EmployeeCapabilityAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("assigned_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("capability", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="employee_assignments", to="accounts.capability")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="companies.company")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="capability_assignments", to=settings.AUTH_USER_MODEL)),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("user", "capability"), name="unique_employee_capability")]},
        ),
        migrations.CreateModel(
            name="InvitationAreaAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("area", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="accounts.workarea")),
                ("invitation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="area_assignments", to="accounts.employeeinvitation")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("invitation", "area"), name="unique_invitation_area")]},
        ),
        migrations.CreateModel(
            name="InvitationCapabilityAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("capability", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="accounts.capability")),
                ("invitation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="capability_assignments", to="accounts.employeeinvitation")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("invitation", "capability"), name="unique_invitation_capability")]},
        ),
        migrations.CreateModel(
            name="InvitationWarehouseAccess",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("access_level", models.CharField(choices=[("view", "Solo consulta"), ("operate", "Operación"), ("manage", "Administración")], default="operate", max_length=20)),
                ("invitation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="warehouse_assignments", to="accounts.employeeinvitation")),
                ("warehouse", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="inventory.warehouse")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("invitation", "warehouse"), name="unique_invitation_warehouse")]},
        ),
        migrations.RunPython(seed_catalogs, migrations.RunPython.noop),
    ]
