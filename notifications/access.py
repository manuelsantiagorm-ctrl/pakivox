from django.db.models import Q

from accounts.effective_access import (
    build_effective_access_context,
    get_request_effective_access_context,
)

from .models import Notification
from .policies import EVENT_NOTIFICATION_POLICIES


def _audience_filter(access):
    """
    Construye una expresion Q acotada por las politicas
    explicitas de los event_type conocidos.

    Nunca recorre usuarios ni materializa una audiencia global.
    """
    audience = None

    for event_type, policy in (
        EVENT_NOTIFICATION_POLICIES.items()
    ):

        # Owner/admin conserva autoridad global dentro de SU empresa,
        # pero incluso ellos solo reciben eventos clasificados.
        if not access.is_owner_or_admin:

            if (
                policy.activity_code
                not in access.activity_codes
            ):
                continue

            required = frozenset(
                policy.required_capabilities
            )

            if not required.issubset(
                access.capability_codes
            ):
                continue

        event_filter = Q(
            event_type=event_type
        )

        if policy.warehouse_scoped:

            warehouse_ids = (
                access.warehouse_ids_for(
                    policy.minimum_level
                )
            )

            if not warehouse_ids:
                continue

            event_filter &= Q(
                warehouse_id__in=warehouse_ids
            )

        else:
            # Un evento company-wide nunca debe heredar
            # accidentalmente datos de una bodega concreta.
            event_filter &= Q(
                warehouse__isnull=True
            )

        audience = (
            event_filter
            if audience is None
            else audience | event_filter
        )

    return audience


def _notifications_for_access(access):
    """
    Resolver unico de visibilidad de Notification.

    Fail closed:
    - sin empresa -> nada;
    - event_type desconocido -> nada;
    - actividad incorrecta -> nada;
    - capability faltante -> nada;
    - nivel insuficiente -> nada;
    - bodega fuera de alcance -> nada.
    """
    if access.company_id is None:
        return Notification.objects.none()

    audience = _audience_filter(
        access
    )

    if audience is None:
        return Notification.objects.none()

    return (
        Notification.objects
        .select_related(
            "company",
            "warehouse",
        )
        .filter(
            company_id=access.company_id,
            event_type__in=(
                EVENT_NOTIFICATION_POLICIES.keys()
            ),
        )
        .filter(
            audience
        )
    )


def notifications_for_user(user):
    """
    Compatibilidad para servicios/tests que solo disponen del user.
    """
    if (
        not user
        or not user.is_authenticated
    ):
        return Notification.objects.none()

    return _notifications_for_access(
        build_effective_access_context(
            user
        )
    )


def notifications_for_request(request):
    """
    Variante preferida para HTTP.

    EffectiveAccessContext se calcula una sola vez por request
    y se reutiliza entre vista, navbar y employee home.
    """
    user = getattr(
        request,
        "user",
        None,
    )

    if (
        not user
        or not user.is_authenticated
    ):
        return Notification.objects.none()

    return _notifications_for_access(
        get_request_effective_access_context(
            request
        )
    )
