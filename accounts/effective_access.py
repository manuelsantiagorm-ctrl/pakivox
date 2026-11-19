from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .capability_access import (
    get_active_user_profile,
    get_user_capability_codes,
)
from .simple_navigation import (
    infer_navigation_activity_codes,
)
from .warehouse_access import (
    ACCESS_APPROVE,
    ACCESS_MANAGE,
    ACCESS_OPERATE,
    ACCESS_VIEW,
    get_accessible_warehouses,
)


ACCESS_LEVELS = (
    ACCESS_VIEW,
    ACCESS_OPERATE,
    ACCESS_APPROVE,
    ACCESS_MANAGE,
)


@dataclass(frozen=True)
class EffectiveAccessContext:
    company_id: int | None
    capability_codes: frozenset[str]
    activity_codes: frozenset[str]
    warehouse_ids_by_level: Mapping[str, frozenset[int]]
    is_owner_or_admin: bool

    def warehouse_ids_for(self, level):
        return self.warehouse_ids_by_level.get(
            level,
            frozenset(),
        )


def _empty_context():
    return EffectiveAccessContext(
        company_id=None,
        capability_codes=frozenset(),
        activity_codes=frozenset(),
        warehouse_ids_by_level=MappingProxyType(
            {
                level: frozenset()
                for level in ACCESS_LEVELS
            }
        ),
        is_owner_or_admin=False,
    )


def build_effective_access_context(user):
    profile = get_active_user_profile(user)

    if profile is None:
        return _empty_context()

    capability_codes = frozenset(
        get_user_capability_codes(user)
    )

    activity_codes = frozenset(
        infer_navigation_activity_codes(
            user,
            capability_codes=capability_codes,
        )
    )

    warehouse_ids_by_level = {}

    from accounts.warehouse_access import user_has_company_wide_warehouse_access

    if user_has_company_wide_warehouse_access(user):
        # Dueño/administrador: el mismo conjunto en todos los niveles. Una
        # consulta en vez de una por nivel en cada página.
        all_ids = frozenset(
            get_accessible_warehouses(
                user=user,
                required_level=ACCESS_LEVELS[0],
            ).values_list("id", flat=True)
        )
        for level in ACCESS_LEVELS:
            warehouse_ids_by_level[level] = all_ids
    else:
        for level in ACCESS_LEVELS:
            warehouse_ids_by_level[level] = frozenset(
                get_accessible_warehouses(
                    user=user,
                    required_level=level,
                ).values_list(
                    "id",
                    flat=True,
                )
            )

    return EffectiveAccessContext(
        company_id=profile.company_id,
        capability_codes=capability_codes,
        activity_codes=activity_codes,
        warehouse_ids_by_level=MappingProxyType(
            warehouse_ids_by_level
        ),
        is_owner_or_admin=bool(
            getattr(
                user,
                "is_superuser",
                False,
            )
            or profile.role in {
                "owner",
                "admin",
            }
        ),
    )


def get_request_effective_access_context(request):
    cache_name = "_pakivox_effective_access_context"

    cached = getattr(
        request,
        cache_name,
        None,
    )

    if cached is not None:
        return cached

    context = build_effective_access_context(
        getattr(
            request,
            "user",
            None,
        )
    )

    setattr(
        request,
        cache_name,
        context,
    )

    return context
