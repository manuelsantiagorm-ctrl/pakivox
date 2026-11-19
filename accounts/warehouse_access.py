from django.core.exceptions import PermissionDenied

from inventory.models import Warehouse

from .models import UserProfile, UserWarehouseAccess

SESSION_WAREHOUSE_KEY = "wms360_active_warehouse"
ALL_WAREHOUSES_VALUE = "all"

ACCESS_VIEW = UserWarehouseAccess.LEVEL_VIEW
ACCESS_OPERATE = UserWarehouseAccess.LEVEL_OPERATE
ACCESS_APPROVE = UserWarehouseAccess.LEVEL_APPROVE
ACCESS_MANAGE = UserWarehouseAccess.LEVEL_MANAGE

VALID_ACCESS_LEVELS = {
    ACCESS_VIEW,
    ACCESS_OPERATE,
    ACCESS_APPROVE,
    ACCESS_MANAGE,
}

ACCESS_LEVELS_ALLOWED = {
    ACCESS_VIEW: {
        ACCESS_VIEW,
        ACCESS_OPERATE,
        ACCESS_APPROVE,
        ACCESS_MANAGE,
    },
    ACCESS_OPERATE: {
        ACCESS_OPERATE,
        ACCESS_APPROVE,
        ACCESS_MANAGE,
    },
    ACCESS_APPROVE: {
        ACCESS_APPROVE,
        ACCESS_MANAGE,
    },
    ACCESS_MANAGE: {
        ACCESS_MANAGE,
    },
}


def get_user_profile(user):
    """
    Devuelve el perfil activo del usuario.

    No utiliza fallbacks a otra empresa y no selecciona la primera
    empresa existente.
    """
    if not user or not user.is_authenticated:
        return None

    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        return None

    if (
        not profile.is_active
        or profile.employment_status != UserProfile.STATUS_ACTIVE
    ):
        return None

    return profile


def get_user_company(user):
    """
    Obtiene exclusivamente la empresa asignada al perfil del usuario de forma segura.
    """
    profile = get_user_profile(user)

    if not profile:
        return None

    # Verificación segura: Si no hay ID de empresa asignada, retorna None sin lanzar excepción
    if not getattr(profile, "company_id", None):
        return None

    try:
        return profile.company
    except Exception:
        return None


def user_has_company_wide_warehouse_access(user):
    """
    Solo el owner tiene acceso implícito a todas las bodegas de su empresa.

    Un superusuario sin perfil de empresa no recibe acceso operativo
    automático para evitar cruces entre empresas.
    """
    profile = get_user_profile(user)

    if not profile:
        return False

    return profile.role == "owner"


def _validate_access_level(required_level):
    if required_level not in VALID_ACCESS_LEVELS:
        raise ValueError(f"Nivel de acceso inválido: {required_level}")


def _active_warehouse_queryset(company):
    queryset = Warehouse.objects.filter(
        company=company,
    )

    warehouse_fields = {field.name for field in Warehouse._meta.get_fields()}

    if "is_active" in warehouse_fields:
        queryset = queryset.filter(
            is_active=True,
        )

    return queryset


def get_accessible_warehouses(
    user,
    required_level=ACCESS_VIEW,
):
    """
    Devuelve únicamente bodegas pertenecientes a la empresa del usuario.

    Owner y admin:
        Todas las bodegas de su empresa.

    Resto de usuarios:
        Solo las bodegas asignadas mediante UserWarehouseAccess.
    """
    _validate_access_level(required_level)

    company = get_user_company(user)

    if not company:
        return Warehouse.objects.none()

    queryset = _active_warehouse_queryset(
        company=company,
    )

    if user_has_company_wide_warehouse_access(user):
        return queryset.order_by(
            "name",
            "id",
        )

    allowed_levels = ACCESS_LEVELS_ALLOWED[required_level]

    return (
        queryset.filter(
            user_accesses__company=company,
            user_accesses__user=user,
            user_accesses__is_active=True,
            user_accesses__access_level__in=allowed_levels,
        )
        .distinct()
        .order_by(
            "name",
            "id",
        )
    )


def user_can_access_warehouse(
    user,
    warehouse,
    required_level=ACCESS_VIEW,
):
    """
    Comprueba empresa, bodega, usuario y nivel de acceso.
    """
    _validate_access_level(required_level)

    if not warehouse:
        return False

    company = get_user_company(user)

    if not company:
        return False

    if warehouse.company_id != company.id:
        return False

    if user_has_company_wide_warehouse_access(user):
        return True

    allowed_levels = ACCESS_LEVELS_ALLOWED[required_level]

    return UserWarehouseAccess.objects.filter(
        company=company,
        user=user,
        warehouse=warehouse,
        is_active=True,
        access_level__in=allowed_levels,
    ).exists()


def get_warehouse_for_user(
    user,
    warehouse_id,
    required_level=ACCESS_VIEW,
):
    """
    Obtiene una bodega únicamente cuando pertenece a la empresa
    y el usuario tiene el nivel solicitado.
    """
    _validate_access_level(required_level)

    try:
        warehouse_id = int(warehouse_id)
    except (TypeError, ValueError):
        raise PermissionDenied("La bodega seleccionada no es válida.")

    warehouse = (
        get_accessible_warehouses(
            user=user,
            required_level=required_level,
        )
        .filter(
            pk=warehouse_id,
        )
        .first()
    )

    if not warehouse:
        raise PermissionDenied("No tienes acceso a la bodega seleccionada.")

    return warehouse


def set_active_warehouse(
    request,
    selected_value,
):
    """
    Guarda en sesión una bodega permitida o el modo global de consulta.
    """
    if not request.user.is_authenticated:
        raise PermissionDenied("Debes iniciar sesión.")

    if str(selected_value) == ALL_WAREHOUSES_VALUE:
        request.session[SESSION_WAREHOUSE_KEY] = ALL_WAREHOUSES_VALUE

        request.session.modified = True
        return None

    warehouse = get_warehouse_for_user(
        user=request.user,
        warehouse_id=selected_value,
        required_level=ACCESS_VIEW,
    )

    request.session[SESSION_WAREHOUSE_KEY] = warehouse.id

    request.session.modified = True

    return warehouse


def clear_active_warehouse(request):
    request.session.pop(
        SESSION_WAREHOUSE_KEY,
        None,
    )

    request.session.modified = True


def get_active_warehouse(
    request,
    allow_all=True,
    auto_select_single=True,
):
    """
    Resuelve la bodega seleccionada en sesión.

    Retorna:
        Warehouse: cuando hay una bodega específica.
        None: cuando está seleccionado "Todas las bodegas".

    Nunca acepta una bodega ajena a la empresa o fuera de los accesos
    del usuario.
    """
    if not request.user.is_authenticated:
        return None

    warehouses = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    )

    selected_value = request.session.get(
        SESSION_WAREHOUSE_KEY,
    )

    warehouse_count = warehouses.count()

    if warehouse_count == 0:
        clear_active_warehouse(request)
        return None

    if selected_value in {
        None,
        "",
    }:
        if auto_select_single and warehouse_count == 1:
            warehouse = warehouses.first()

            request.session[SESSION_WAREHOUSE_KEY] = warehouse.id

            request.session.modified = True
            return warehouse

        if allow_all:
            request.session[SESSION_WAREHOUSE_KEY] = ALL_WAREHOUSES_VALUE

            request.session.modified = True
            return None

        raise PermissionDenied("Selecciona una bodega específica.")

    if str(selected_value) == ALL_WAREHOUSES_VALUE:
        if allow_all:
            return None

        raise PermissionDenied(
            "Selecciona una bodega específica para realizar esta operación."
        )

    try:
        selected_id = int(selected_value)
    except (TypeError, ValueError):
        clear_active_warehouse(request)

        raise PermissionDenied("La bodega seleccionada dejó de ser válida.")

    warehouse = warehouses.filter(
        pk=selected_id,
    ).first()

    if warehouse:
        return warehouse

    clear_active_warehouse(request)

    if auto_select_single and warehouse_count == 1:
        warehouse = warehouses.first()

        request.session[SESSION_WAREHOUSE_KEY] = warehouse.id

        request.session.modified = True
        return warehouse

    if allow_all:
        request.session[SESSION_WAREHOUSE_KEY] = ALL_WAREHOUSES_VALUE

        request.session.modified = True
        return None

    raise PermissionDenied("No tienes acceso a la bodega seleccionada.")


def require_active_warehouse(
    request,
    required_level=ACCESS_OPERATE,
):
    """
    Exige una bodega concreta.

    Bloquea automáticamente el modo Todas las bodegas.
    """
    warehouse = get_active_warehouse(
        request=request,
        allow_all=False,
    )

    if not warehouse:
        raise PermissionDenied("Selecciona una bodega específica.")

    if not user_can_access_warehouse(
        user=request.user,
        warehouse=warehouse,
        required_level=required_level,
    ):
        raise PermissionDenied(
            "No tienes permisos suficientes para operar esta bodega."
        )

    return warehouse


def build_warehouse_context(request):
    """
    Contexto reutilizable para templates y selector global.
    """
    empty_context = {
        "warehouse_company": None,
        "accessible_warehouses": Warehouse.objects.none(),
        "active_warehouse": None,
        "active_warehouse_is_all": False,
        "active_warehouse_value": "",
        "can_operate_active_warehouse": False,
        "can_approve_active_warehouse": False,
        "can_manage_active_warehouse": False,
    }

    if not request.user.is_authenticated:
        return empty_context

    company = get_user_company(
        request.user,
    )

    if not company:
        return empty_context

    warehouses = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    )

    active_warehouse = get_active_warehouse(
        request=request,
        allow_all=True,
    )

    selected_value = request.session.get(
        SESSION_WAREHOUSE_KEY,
        "",
    )

    is_all = str(selected_value) == ALL_WAREHOUSES_VALUE

    can_operate = False
    can_approve = False
    can_manage = False

    if active_warehouse:
        can_operate = user_can_access_warehouse(
            user=request.user,
            warehouse=active_warehouse,
            required_level=ACCESS_OPERATE,
        )

        can_manage = user_can_access_warehouse(
            user=request.user,
            warehouse=active_warehouse,
            required_level=ACCESS_MANAGE,
        )
        can_approve = user_can_access_warehouse(
            user=request.user,
            warehouse=active_warehouse,
            required_level=ACCESS_APPROVE,
        )

    return {
        "warehouse_company": company,
        "accessible_warehouses": warehouses,
        "active_warehouse": active_warehouse,
        "active_warehouse_is_all": is_all,
        "active_warehouse_value": selected_value,
        "can_operate_active_warehouse": can_operate,
        "can_approve_active_warehouse": can_approve,
        "can_manage_active_warehouse": can_manage,
    }
