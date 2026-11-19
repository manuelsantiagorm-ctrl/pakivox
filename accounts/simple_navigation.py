from django.urls import reverse

from .capability_access import (
    get_active_user_profile,
    get_user_capability_codes,
)
from .models import EmployeeJobAssignment
from .simple_activity_contract import entry_view_for_activity
from .simple_activity_route_contract import (
    SCOPE_MULTIWAREHOUSE,
    SCOPE_TRANSFER,
    SCOPE_WAREHOUSE,
    route_action_contract,
)
from .simple_employee_access import (
    ACTIVITIES,
    ACTIVITY_BY_CODE,
    DIRECT_CAPABILITY_ACTIVITY_MAP,
    activity_codes_from_job_codes,
)
from .warehouse_access import (
    ACCESS_VIEW,
    get_accessible_warehouses,
)


WAREHOUSE_SCOPES = frozenset(
    {
        SCOPE_WAREHOUSE,
        SCOPE_MULTIWAREHOUSE,
        SCOPE_TRANSFER,
    }
)


TOOL_NAVIGATION = (
    {
        "codes": frozenset(
            {
                "messaging.operate",
                "messaging.supervise",
            }
        ),
        "code": "messaging",
        "name": "Conversaciones",
        "description": "Chats y atención por WhatsApp.",
        "icon": "bi-whatsapp",
        "view_name": "messaging:chat_inbox",
    },
    {
        "codes": frozenset(
            {
                "messaging.configure",
            }
        ),
        "code": "messaging_config",
        "name": "Canales WhatsApp",
        "description": "Configura números y canales autorizados.",
        "icon": "bi-broadcast",
        "view_name": "messaging:channels_list",
    },
    {
        "codes": frozenset(
            {
                "shipping.configure",
            }
        ),
        "code": "shipping_config",
        "name": "Configuración de envíos",
        "description": "Integraciones y orígenes de paquetería.",
        "icon": "bi-truck-front-fill",
        "view_name": "shipping_hub:integrations_list",
    },
    {
        "codes": frozenset(
            {
                "shipping.view",
                "shipping.execute",
            }
        ),
        "code": "shipping_print_center",
        "name": "Centro de impresión",
        "description": "Imprime etiquetas de envío en lote.",
        "icon": "bi-printer",
        "view_name": "shipping_hub:print_center",
    },
)


def _ordered_activity_codes(selected):
    selected = set(selected or ())
    return tuple(
        activity.code
        for activity in ACTIVITIES
        if activity.code in selected
    )


def infer_navigation_activity_codes(
    user,
    *,
    capability_codes=None,
):
    """
    Traduce el acceso actual a las nueve actividades humanas.

    No autoriza nada. La autorización real sigue en backend.
    Sólo construye navegación compatible con:
    - actividades PS-1;
    - puestos legacy conocidos;
    - grants directos legacy representativos.
    """
    profile = get_active_user_profile(user)

    if (
        profile is None
        or profile.role == "owner"
        or getattr(user, "is_superuser", False)
    ):
        return tuple()

    if capability_codes is None:
        capability_codes = get_user_capability_codes(user)

    job_codes = EmployeeJobAssignment.objects.filter(
        company_id=profile.company_id,
        user_id=user.id,
        is_active=True,
        job_template__is_active=True,
    ).values_list(
        "job_template__code",
        flat=True,
    )

    selected = set(
        activity_codes_from_job_codes(job_codes)
    )

    # Compatibilidad para grants directos representativos.
    # Sólo se usan mappings no ambiguos del catálogo PS-1.
    for capability_code in capability_codes:
        activity_code = DIRECT_CAPABILITY_ACTIVITY_MAP.get(
            capability_code
        )
        if activity_code:
            selected.add(activity_code)

    return _ordered_activity_codes(selected)


def build_simple_navigation_items(
    user,
    *,
    capability_codes=None,
    has_warehouse_access=None,
):
    profile = get_active_user_profile(user)

    if (
        profile is None
        or profile.role == "owner"
        or getattr(user, "is_superuser", False)
    ):
        return tuple()

    if capability_codes is None:
        capability_codes = get_user_capability_codes(user)

    capability_codes = frozenset(capability_codes)

    if has_warehouse_access is None:
        has_warehouse_access = get_accessible_warehouses(
            user=user,
            required_level=ACCESS_VIEW,
        ).exists()

    items = []

    for activity_code in infer_navigation_activity_codes(
        user,
        capability_codes=capability_codes,
    ):
        activity = ACTIVITY_BY_CODE[activity_code]
        view_name = entry_view_for_activity(activity_code)
        source, route_name = view_name.split(":", 1)
        route_contract = route_action_contract(
            source,
            route_name,
        )

        if (
            route_contract.capability_code
            and route_contract.capability_code
            not in capability_codes
        ):
            # Un deny explícito o la falta del permiso de lectura de entrada
            # nunca debe dejar un enlace que termine en 403.
            continue

        if (
            route_contract.scope in WAREHOUSE_SCOPES
            and not has_warehouse_access
        ):
            continue

        items.append(
            {
                "code": activity.code,
                "name": activity.label,
                "label": activity.label,
                "description": activity.description,
                "icon": activity.icon,
                "view_name": view_name,
                "url": reverse(view_name),
            }
        )

    return tuple(items)


def build_simple_tool_navigation_items(
    *,
    capability_codes,
):
    capability_codes = frozenset(capability_codes or ())
    items = []

    for definition in TOOL_NAVIGATION:
        if not (
            definition["codes"]
            & capability_codes
        ):
            continue

        item = dict(definition)
        item["url"] = reverse(
            definition["view_name"]
        )
        items.append(item)

    return tuple(items)


def build_simple_navigation_context(
    request,
    *,
    capability_codes=None,
    has_warehouse_access=None,
):
    user = getattr(request, "user", None)
    profile = get_active_user_profile(user)

    if (
        profile is None
        or profile.role == "owner"
        or getattr(user, "is_superuser", False)
    ):
        return {
            "simple_navigation_enabled": False,
            "simple_nav_items": tuple(),
            "simple_tool_nav_items": tuple(),
        }

    if capability_codes is None:
        capability_codes = get_user_capability_codes(user)

    return {
        "simple_navigation_enabled": True,
        "simple_nav_items": build_simple_navigation_items(
            user,
            capability_codes=capability_codes,
            has_warehouse_access=has_warehouse_access,
        ),
        "simple_tool_nav_items": build_simple_tool_navigation_items(
            capability_codes=capability_codes,
        ),
    }
