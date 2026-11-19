from django.db import migrations


AREAS = (
    ("sales_service", "Ventas y atención", 10),
    ("collections_payments", "Cobranza y pagos", 20),
    ("accounting_finance", "Contabilidad y finanzas", 30),
    ("purchasing_suppliers", "Compras y proveedores", 40),
    ("inventory_warehouse", "Inventario y almacén", 50),
    ("logistics_delivery", "Logística y entregas", 60),
    ("company_admin", "Administración de empresa", 70),
    ("executive_direction", "Dirección y CEO", 80),
)

CAPABILITIES = (
    ("dashboard.view", "Consultar dashboard CEO", "executive_direction"),
    ("control_tower.view", "Consultar Mesa de Control", "executive_direction"),
    ("team.manage", "Administrar equipo", "company_admin"),
    ("company.configure", "Configurar empresa y bodegas", "company_admin"),
    ("integrations.configure", "Configurar integraciones", "company_admin"),
    ("imports.manage", "Ejecutar importaciones", "company_admin"),
    ("sales.view", "Consultar pedidos", "sales_service"),
    ("sales.create", "Crear pedidos", "sales_service"),
    ("sales.supervise", "Supervisar ventas del equipo", "sales_service"),
    ("crm.manage", "Gestionar clientes y oportunidades", "sales_service"),
    ("messaging.operate", "Atender WhatsApp", "sales_service"),
    ("messaging.supervise", "Supervisar y reasignar conversaciones", "sales_service"),
    ("messaging.configure", "Configurar canales y agentes", "company_admin"),
    ("payments.view", "Consultar pagos", "collections_payments"),
    ("payments.approve", "Aprobar o rechazar pagos", "collections_payments"),
    ("collections.manage", "Gestionar cobranza y saldos", "collections_payments"),
    ("accounting.view", "Consultar contabilidad", "accounting_finance"),
    ("accounting.manage", "Registrar y cerrar contabilidad", "accounting_finance"),
    ("purchases.manage", "Gestionar compras", "purchasing_suppliers"),
    ("suppliers.manage", "Gestionar proveedores", "purchasing_suppliers"),
    ("receiving.execute", "Recibir mercancía", "inventory_warehouse"),
    ("inventory.view", "Consultar inventario", "inventory_warehouse"),
    ("inventory.manage", "Administrar inventario", "inventory_warehouse"),
    ("picking.execute", "Surtir pedidos", "inventory_warehouse"),
    ("packing.execute", "Empacar pedidos", "inventory_warehouse"),
    ("quality.approve", "Aprobar control de calidad", "inventory_warehouse"),
    ("shipping.view", "Consultar envíos y tracking", "logistics_delivery"),
    ("shipping.execute", "Despachar y confirmar entregas", "logistics_delivery"),
    ("shipping.quote", "Consultar tarifas de envío", "logistics_delivery"),
    ("shipping.configure", "Configurar API propia de envíos", "company_admin"),
)

TEMPLATES = (
    ("vendedor", "Vendedor", "sales", (
        "sales.view", "sales.create", "crm.manage", "messaging.operate",
        "inventory.view", "shipping.view",
    )),
    ("gerente_ventas", "Gerente de ventas", "manager", (
        "sales.view", "sales.create", "sales.supervise", "crm.manage",
        "messaging.operate", "messaging.supervise", "inventory.view",
        "shipping.view",
    )),
    ("cajero", "Caja y cobranza", "accounting", (
        "payments.view", "payments.approve", "collections.manage",
    )),
    ("supervisor_cobranza", "Supervisor de cobranza", "manager", (
        "payments.view", "payments.approve", "collections.manage",
    )),
    ("contador", "Contabilidad", "accounting", (
        "accounting.view", "accounting.manage", "payments.view",
    )),
    ("gerente_financiero", "Gerente financiero", "manager", (
        "accounting.view", "accounting.manage", "payments.view",
        "collections.manage",
    )),
    ("comprador", "Comprador", "manager", (
        "purchases.manage", "suppliers.manage", "inventory.view",
    )),
    ("gerente_compras", "Gerente de compras", "manager", (
        "purchases.manage", "suppliers.manage", "inventory.view",
    )),
    ("receptor", "Recepción", "warehouse", (
        "receiving.execute", "inventory.view",
    )),
    ("surtidor", "Surtidor", "warehouse", (
        "inventory.view", "picking.execute",
    )),
    ("empacador", "Empacador", "warehouse", (
        "inventory.view", "packing.execute",
    )),
    ("calidad", "Control de calidad", "warehouse", (
        "inventory.view", "quality.approve", "packing.execute",
    )),
    ("supervisor", "Supervisor de almacén", "manager", (
        "inventory.view", "inventory.manage", "receiving.execute",
        "picking.execute", "packing.execute", "quality.approve",
    )),
    ("coordinador_logistico", "Coordinador logístico", "manager", (
        "shipping.view", "shipping.execute", "shipping.quote",
    )),
    ("despachador", "Despachador", "warehouse", (
        "shipping.view", "shipping.execute", "shipping.quote",
    )),
    ("repartidor", "Repartidor", "warehouse", (
        "shipping.view", "shipping.execute",
    )),
    ("administrador_empresa", "Administrador de empresa", "admin", (
        "team.manage", "company.configure", "integrations.configure",
        "imports.manage", "messaging.configure", "shipping.configure",
    )),
    ("gerente_general", "Gerente general / CEO", "manager", (
        "dashboard.view", "control_tower.view", "sales.view",
        "sales.supervise", "payments.view", "collections.manage",
        "accounting.view", "purchases.manage", "inventory.view",
        "shipping.view",
    )),
)

LEGACY_AREA_MAP = {
    "sales": ("sales_service",),
    "finance": ("collections_payments", "accounting_finance"),
    "purchases": ("purchasing_suppliers", "inventory_warehouse"),
    "warehouse": ("inventory_warehouse",),
    "packing": ("inventory_warehouse", "logistics_delivery"),
    "direction": ("company_admin", "executive_direction"),
}


def seed_eight_areas(apps, schema_editor):
    Company = apps.get_model("companies", "Company")
    Capability = apps.get_model("accounts", "Capability")
    WorkArea = apps.get_model("accounts", "WorkArea")
    JobTemplate = apps.get_model("accounts", "JobTemplate")
    EmployeeAreaAssignment = apps.get_model("accounts", "EmployeeAreaAssignment")
    EmployeeCapabilityAssignment = apps.get_model(
        "accounts", "EmployeeCapabilityAssignment"
    )

    capabilities = {}
    for code, name, area_code in CAPABILITIES:
        capability, _ = Capability.objects.update_or_create(
            code=code,
            defaults={"name": name, "area_code": area_code, "is_active": True},
        )
        capabilities[code] = capability

    for company in Company.objects.all().iterator():
        company_areas = {}
        for code, name, sort_order in AREAS:
            area, _ = WorkArea.objects.update_or_create(
                company=company,
                code=code,
                defaults={
                    "name": name,
                    "sort_order": sort_order,
                    "is_active": True,
                },
            )
            company_areas[code] = area

        old_assignments = EmployeeAreaAssignment.objects.filter(
            company=company,
            is_active=True,
            area__code__in=LEGACY_AREA_MAP,
        ).values_list("user_id", "area__code", "assigned_by_id")
        for user_id, old_code, assigned_by_id in old_assignments:
            for new_code in LEGACY_AREA_MAP[old_code]:
                EmployeeAreaAssignment.objects.update_or_create(
                    company=company,
                    user_id=user_id,
                    area=company_areas[new_code],
                    defaults={
                        "assigned_by_id": assigned_by_id,
                        "is_active": True,
                    },
                )

        WorkArea.objects.filter(
            company=company,
            code__in=LEGACY_AREA_MAP,
        ).update(is_active=False)

        for code, name, role, capability_codes in TEMPLATES:
            template, _ = JobTemplate.objects.update_or_create(
                company=company,
                code=code,
                defaults={
                    "name": name,
                    "role": role,
                    "is_system": True,
                    "is_active": True,
                },
            )
            template.capabilities.set(
                [capabilities[item] for item in capability_codes]
            )

        seller_ids = EmployeeCapabilityAssignment.objects.filter(
            company=company,
            is_active=True,
            capability__code="sales.create",
        ).values_list("user_id", flat=True)
        crm_seller_ids = set(
            EmployeeCapabilityAssignment.objects.filter(
                company=company,
                user_id__in=seller_ids,
                is_active=True,
                capability__code="crm.manage",
            ).values_list("user_id", flat=True)
        )
        for user_id in crm_seller_ids:
            EmployeeCapabilityAssignment.objects.update_or_create(
                company=company,
                user_id=user_id,
                capability=capabilities["messaging.operate"],
                defaults={"is_active": True},
            )


class Migration(migrations.Migration):
    dependencies = [
        (
            "accounts",
            "0007_alter_capability_id_alter_employeeareaassignment_id_and_more",
        ),
    ]

    operations = [
        migrations.RunPython(seed_eight_areas, migrations.RunPython.noop),
    ]
