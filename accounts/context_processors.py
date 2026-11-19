from .capability_access import build_capability_context
from .simple_navigation import build_simple_navigation_context
from .warehouse_access import build_warehouse_context


def warehouse_context(request):
    capability_context = build_capability_context(
        request=request,
    )

    context = build_warehouse_context(
        request=request,
    )

    context.update(
        capability_context
    )

    has_warehouse_access = bool(
        context.get("active_warehouse")
        or context.get("active_warehouse_is_all")
    )

    context.update(
        build_simple_navigation_context(
            request=request,
            capability_codes=capability_context[
                "user_capability_codes"
            ],
            has_warehouse_access=has_warehouse_access,
        )
    )

    return context
