from django.db import migrations


CAPABILITIES = (
    # =========================================================
    # COMPRAS
    # =========================================================
    (
        "purchases.view",
        "Consultar órdenes de compra",
        "purchasing_suppliers",
    ),
    (
        "purchases.create",
        "Crear órdenes de compra",
        "purchasing_suppliers",
    ),
    (
        "purchases.edit",
        "Editar órdenes de compra",
        "purchasing_suppliers",
    ),
    (
        "purchases.approve",
        "Autorizar y enviar órdenes de compra",
        "purchasing_suppliers",
    ),
    (
        "purchases.cancel",
        "Cancelar órdenes de compra",
        "purchasing_suppliers",
    ),
    (
        "purchases.costs.view",
        "Consultar costos de compra",
        "purchasing_suppliers",
    ),
    (
        "purchases.costs.edit",
        "Modificar costos de compra",
        "purchasing_suppliers",
    ),

    # =========================================================
    # PROVEEDORES
    # =========================================================
    (
        "suppliers.view",
        "Consultar proveedores",
        "purchasing_suppliers",
    ),
    (
        "suppliers.manage",
        "Gestionar proveedores",
        "purchasing_suppliers",
    ),

    # =========================================================
    # RECEPCIONES
    # =========================================================
    (
        "receiving.view",
        "Consultar recepciones de mercancía",
        "inventory_warehouse",
    ),
    (
        "receiving.execute",
        "Capturar recepción de mercancía",
        "inventory_warehouse",
    ),
    (
        "receiving.confirm",
        "Confirmar entrada de mercancía",
        "inventory_warehouse",
    ),
    (
        "receiving.cancel",
        "Cancelar recepciones de mercancía",
        "inventory_warehouse",
    ),

    # =========================================================
    # PAGOS A PROVEEDORES
    # =========================================================
    (
        "supplier_payments.view",
        "Consultar pagos a proveedores",
        "accounting_finance",
    ),
    (
        "supplier_payments.create",
        "Registrar pagos a proveedores",
        "accounting_finance",
    ),
    (
        "supplier_payments.proof.view",
        "Consultar comprobantes de pagos a proveedores",
        "accounting_finance",
    ),
    (
        "supplier_payments.approve",
        "Aprobar pagos a proveedores",
        "accounting_finance",
    ),
    (
        "supplier_payments.reject",
        "Rechazar pagos a proveedores",
        "accounting_finance",
    ),
    (
        "supplier_payments.cancel",
        "Cancelar pagos a proveedores",
        "accounting_finance",
    ),
)


PURCHASE_BASIC = (
    "purchases.view",
    "purchases.create",
    "purchases.edit",
    "purchases.costs.view",
    "purchases.costs.edit",
    "suppliers.view",
    "suppliers.manage",
)

PURCHASE_APPROVAL = (
    *PURCHASE_BASIC,
    "purchases.approve",
    "purchases.cancel",
)

RECEIVING_BASIC = (
    "receiving.view",
    "receiving.execute",
)

RECEIVING_SUPERVISION = (
    *RECEIVING_BASIC,
    "receiving.confirm",
    "receiving.cancel",
)

SUPPLIER_PAYMENT_REGISTRATION = (
    "supplier_payments.view",
    "supplier_payments.create",
    "supplier_payments.proof.view",
)

SUPPLIER_PAYMENT_APPROVAL = (
    *SUPPLIER_PAYMENT_REGISTRATION,
    "supplier_payments.approve",
    "supplier_payments.reject",
    "supplier_payments.cancel",
)


def seed_purchase_permissions(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    JobTemplate = apps.get_model("accounts", "JobTemplate")
    EmployeeCapabilityAssignment = apps.get_model(
        "accounts",
        "EmployeeCapabilityAssignment",
    )

    capabilities = {}

    # ---------------------------------------------------------
    # Crear / actualizar catálogo
    # ---------------------------------------------------------
    for code, name, area_code in CAPABILITIES:
        capability, _ = Capability.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "area_code": area_code,
                "is_active": True,
            },
        )

        capabilities[code] = capability

    def add_to_template(template_code, capability_codes):
        for template in JobTemplate.objects.filter(
            code=template_code,
            is_active=True,
        ).iterator():
            template.capabilities.add(
                *[
                    capabilities[code]
                    for code in capability_codes
                ]
            )

    # =========================================================
    # PUESTOS DE COMPRAS
    # =========================================================

    # Comprador:
    # crea y modifica compras, proveedores y costos;
    # NO aprueba ni cancela automáticamente.
    add_to_template(
        "comprador",
        PURCHASE_BASIC,
    )

    # Gerente de compras:
    # controla el ciclo completo de la orden.
    add_to_template(
        "gerente_compras",
        (
            *PURCHASE_APPROVAL,
            *SUPPLIER_PAYMENT_REGISTRATION,
        ),
    )

    # =========================================================
    # RECEPCIÓN / ALMACÉN
    # =========================================================

    # Receptor captura mercancía pero no confirma
    # definitivamente stock sin autorización superior.
    add_to_template(
        "receptor",
        RECEIVING_BASIC,
    )

    # Supervisor de almacén puede confirmar/cancelar.
    add_to_template(
        "supervisor",
        RECEIVING_SUPERVISION,
    )

    # =========================================================
    # FINANZAS
    # =========================================================

    # Contabilidad registra y consulta pagos a proveedor.
    add_to_template(
        "contador",
        SUPPLIER_PAYMENT_REGISTRATION,
    )

    # Gerencia financiera puede aprobar acciones financieras.
    add_to_template(
        "gerente_financiero",
        (
            *SUPPLIER_PAYMENT_APPROVAL,
            "purchases.view",
            "purchases.costs.view",
            "suppliers.view",
        ),
    )

    # =========================================================
    # DIRECCIÓN
    # =========================================================

    add_to_template(
        "gerente_general",
        (
            "purchases.view",
            "purchases.approve",
            "purchases.cancel",
            "purchases.costs.view",
            "suppliers.view",
            "receiving.view",
            *SUPPLIER_PAYMENT_APPROVAL,
        ),
    )

    # =========================================================
    # COMPATIBILIDAD CON ASIGNACIONES INDIVIDUALES ANTERIORES
    # =========================================================
    #
    # Una persona que recibió explícitamente purchases.manage
    # conserva temporalmente las facultades que ya tenía.
    #
    # Las NUEVAS asignaciones serán granulares.
    # =========================================================

    legacy_purchase_rows = EmployeeCapabilityAssignment.objects.filter(
        is_active=True,
        capability__code="purchases.manage",
    ).values_list(
        "company_id",
        "user_id",
        "assigned_by_id",
    )

    legacy_purchase_codes = (
        *PURCHASE_APPROVAL,
        *SUPPLIER_PAYMENT_APPROVAL,
    )

    for company_id, user_id, assigned_by_id in legacy_purchase_rows.iterator():
        for code in legacy_purchase_codes:
            EmployeeCapabilityAssignment.objects.update_or_create(
                company_id=company_id,
                user_id=user_id,
                capability=capabilities[code],
                defaults={
                    "assigned_by_id": assigned_by_id,
                    "is_active": True,
                },
            )

    # ---------------------------------------------------------
    # Compatibilidad proveedores
    # ---------------------------------------------------------

    legacy_supplier_rows = EmployeeCapabilityAssignment.objects.filter(
        is_active=True,
        capability__code="suppliers.manage",
    ).values_list(
        "company_id",
        "user_id",
        "assigned_by_id",
    )

    for company_id, user_id, assigned_by_id in legacy_supplier_rows.iterator():
        EmployeeCapabilityAssignment.objects.update_or_create(
            company_id=company_id,
            user_id=user_id,
            capability=capabilities["suppliers.view"],
            defaults={
                "assigned_by_id": assigned_by_id,
                "is_active": True,
            },
        )

    # ---------------------------------------------------------
    # Compatibilidad recepción
    # ---------------------------------------------------------

    legacy_receiving_rows = EmployeeCapabilityAssignment.objects.filter(
        is_active=True,
        capability__code="receiving.execute",
    ).values_list(
        "company_id",
        "user_id",
        "assigned_by_id",
    )

    for company_id, user_id, assigned_by_id in legacy_receiving_rows.iterator():
        for code in RECEIVING_SUPERVISION:
            EmployeeCapabilityAssignment.objects.update_or_create(
                company_id=company_id,
                user_id=user_id,
                capability=capabilities[code],
                defaults={
                    "assigned_by_id": assigned_by_id,
                    "is_active": True,
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_payment_permissions"),
    ]

    operations = [
        migrations.RunPython(
            seed_purchase_permissions,
            migrations.RunPython.noop,
        ),
    ]
