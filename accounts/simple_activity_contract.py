from dataclasses import dataclass

from .warehouse_access import (
    ACCESS_APPROVE,
    ACCESS_MANAGE,
    ACCESS_OPERATE,
    ACCESS_VIEW,
)


AUTHORITY_LEVELS = (
    ACCESS_VIEW,
    ACCESS_OPERATE,
    ACCESS_APPROVE,
    ACCESS_MANAGE,
)

# Estas capabilities nunca deben entrar automáticamente a una actividad humana,
# ni siquiera con nivel "Administrar". Pertenecen a administración de empresa,
# dirección o configuración técnica y se conceden por puestos/overrides explícitos.
OUT_OF_ACTIVITY_SCOPE_CAPABILITIES = frozenset(
    {
        "company.configure",
        "control_tower.view",
        "dashboard.view",
        "imports.manage",
        "integrations.configure",
        "messaging.configure",
        "shipping.configure",
        "team.manage",
    }
)


@dataclass(frozen=True)
class ActivityAccessContract:
    code: str
    label: str
    entry_view_name: str
    baseline_level: str
    capability_steps: tuple[tuple[str, tuple[str, ...]], ...]
    note: str = ""

    def capabilities_added_at(self, level):
        for candidate, codes in self.capability_steps:
            if candidate == level:
                return codes
        raise ValueError(f"Nivel de autoridad inválido: {level}")

    def capabilities_through(self, level):
        if level not in AUTHORITY_LEVELS:
            raise ValueError(f"Nivel de autoridad inválido: {level}")

        collected = []
        seen = set()
        for candidate in AUTHORITY_LEVELS:
            for code in self.capabilities_added_at(candidate):
                if code not in seen:
                    collected.append(code)
                    seen.add(code)
            if candidate == level:
                break
        return tuple(collected)


# PS-2A define el contrato objetivo. No cambia todavía cómo resolve_capability()
# materializa o evalúa estos niveles. PS-3 será el gate que haga efectiva la
# autoridad Consultar/Trabajar/Supervisar/Administrar en todos los módulos.
ACTIVITY_ACCESS_CONTRACTS = (
    ActivityAccessContract(
        code="sales_customers",
        label="Ventas y clientes",
        entry_view_name="sales:lista_pedidos",
        baseline_level=ACCESS_MANAGE,
        capability_steps=(
            (ACCESS_VIEW, ("sales.view",)),
            (ACCESS_OPERATE, ("sales.create", "crm.manage", "messaging.operate")),
            (
                ACCESS_APPROVE,
                (
                    "sales.supervise",
                    "sales.recover_order",
                    "sales.cancel_order",
                ),
            ),
            (ACCESS_MANAGE, ("sales.commercial_cancel",)),
        ),
        note=(
            "El catálogo actual solo tiene crm.manage; no existe crm.view. "
            "PS-3 debe evitar tratar crm.manage como permiso de solo consulta."
        ),
    ),
    ActivityAccessContract(
        code="collections",
        label="Cobros",
        entry_view_name="payments:lista_pagos",
        baseline_level=ACCESS_MANAGE,
        capability_steps=(
            (ACCESS_VIEW, ("payments.view", "payments.proof.view")),
            (ACCESS_OPERATE, ("payments.create",)),
            (ACCESS_APPROVE, ("payments.approve", "payments.cancel")),
            (
                ACCESS_MANAGE,
                (
                    "collections.manage",
                    "payments.refund",
                    "payments.refund.confirm",
                    "payments.customer_credit.manage",
                    "payments.customer_credit.apply",
                    "messaging.operate",
                ),
            ),
        ),
    ),
    ActivityAccessContract(
        code="purchasing",
        label="Compras",
        entry_view_name="purchases:lista_compras",
        baseline_level=ACCESS_MANAGE,
        capability_steps=(
            (ACCESS_VIEW, ("purchases.view", "suppliers.view")),
            (
                ACCESS_OPERATE,
                (
                    "purchases.create",
                    "purchases.edit",
                    "suppliers.manage",
                ),
            ),
            (ACCESS_APPROVE, ("purchases.approve", "purchases.cancel")),
            (
                ACCESS_MANAGE,
                (
                    "purchases.costs.view",
                    "purchases.costs.edit",
                ),
            ),
        ),
        note=(
            "purchases.manage NO es parte del baseline de esta actividad: "
            "no está en el catálogo de capabilities activas usadas por "
            "PS-1 y se concede únicamente vía EmployeeCapabilityOverride "
            "si en el futuro se activa."
        ),
    ),
    ActivityAccessContract(
        code="receiving",
        label="Recepción",
        # No existe todavía una lista independiente de recepciones. Stock es
        # el destino navegable seguro que ya forma parte del baseline actual.
        entry_view_name="purchases:lista_recepciones",
        baseline_level=ACCESS_MANAGE,
        capability_steps=(
            (ACCESS_VIEW, ("receiving.view",)),
            (ACCESS_OPERATE, ("receiving.execute",)),
            (ACCESS_APPROVE, ("receiving.confirm",)),
            (ACCESS_MANAGE, ("receiving.cancel",)),
        ),
        note=(
            "Falta una pantalla raíz propia de Recepción; PS-4/PS-6 deberá "
            "crear o elegir una navegación dedicada. quality.approve NO es "
            "parte del baseline de esta actividad: es orphan (solo "
            "owner/admin) y se concede únicamente vía "
            "EmployeeCapabilityOverride a personas específicas."
        ),
    ),
    ActivityAccessContract(
        code="inventory",
        label="Inventario",
        entry_view_name="inventory:lista_stock",
        baseline_level=ACCESS_MANAGE,
        capability_steps=(
            (ACCESS_VIEW, ("inventory.view",)),
            (ACCESS_OPERATE, ()),
            (ACCESS_APPROVE, ()),
            (ACCESS_MANAGE, ("inventory.manage",)),
        ),
        note=("El catálogo actual no separa inventory.operate / inventory.approve."),
    ),
    ActivityAccessContract(
        code="picking",
        label="Preparar pedidos",
        entry_view_name="warehouse:lista_picking",
        baseline_level=ACCESS_OPERATE,
        capability_steps=(
            (ACCESS_VIEW, ("picking.view",)),
            (ACCESS_OPERATE, ("picking.execute",)),
            (ACCESS_APPROVE, ()),
            (ACCESS_MANAGE, ()),
        ),
        note=(
            "La diferencia entre niveles superiores debe depender del nivel "
            "de bodega y de gates por acción en PS-3."
        ),
    ),
    ActivityAccessContract(
        code="packing",
        label="Empacar",
        entry_view_name="warehouse:lista_packing",
        baseline_level=ACCESS_OPERATE,
        capability_steps=(
            (ACCESS_VIEW, ("packing.view",)),
            (ACCESS_OPERATE, ("packing.execute",)),
            (ACCESS_APPROVE, ()),
            (ACCESS_MANAGE, ()),
        ),
        note=(
            "La diferencia entre niveles superiores debe depender del nivel "
            "de bodega y de gates por acción en PS-3."
        ),
    ),
    ActivityAccessContract(
        code="shipping",
        label="Envíos",
        entry_view_name="warehouse:lista_shipping",
        baseline_level=ACCESS_OPERATE,
        capability_steps=(
            (ACCESS_VIEW, ("shipping.view",)),
            (
                ACCESS_OPERATE,
                (
                    "shipping.quote",
                    "shipping.execute",
                    "shipping.collect",
                    "shipping.incidents.resolve",
                ),
            ),
            (ACCESS_APPROVE, ()),
            (ACCESS_MANAGE, ()),
        ),
        note=(
            "shipping.execute se combina con required_level en Shipping Hub. "
            "shipping.configure queda fuera de las actividades humanas. "
            "shipping.collect y shipping.incidents.resolve se incluyen desde "
            "Trabajar: recolectar lotes y resolver incidencias son tareas "
            "normales del área Envíos."
        ),
    ),
    ActivityAccessContract(
        code="accounting",
        label="Contabilidad",
        entry_view_name="accounting:financial_dashboard",
        baseline_level=ACCESS_MANAGE,
        capability_steps=(
            (
                ACCESS_VIEW,
                (
                    "accounting.view",
                    "supplier_payments.view",
                    "supplier_payments.proof.view",
                ),
            ),
            (
                ACCESS_OPERATE,
                ("accounting.manage", "supplier_payments.create"),
            ),
            (
                ACCESS_APPROVE,
                ("supplier_payments.approve", "supplier_payments.reject"),
            ),
            (
                ACCESS_MANAGE,
                ("supplier_payments.cancel",),
            ),
        ),
        note=(
            "finance.costs.view NO es parte del baseline de esta actividad: "
            "es orphan (solo owner/admin) y se concede únicamente vía "
            "EmployeeCapabilityOverride a personas específicas."
        ),
    ),
)

ACTIVITY_CONTRACT_BY_CODE = {
    item.code: item
    for item in ACTIVITY_ACCESS_CONTRACTS
}


def activity_contract(activity_code):
    try:
        return ACTIVITY_CONTRACT_BY_CODE[activity_code]
    except KeyError as exc:
        raise ValueError(f"Actividad humana inválida: {activity_code}") from exc


def capabilities_for_activity_level(activity_code, level):
    return activity_contract(activity_code).capabilities_through(level)


def entry_view_for_activity(activity_code):
    return activity_contract(activity_code).entry_view_name
