from django.core.exceptions import ValidationError

from inventory.models import Warehouse
from warehouse.models import OrderFulfillment, PickingOrder


def _validate_order(order):
    if order is None:
        raise ValidationError("Debes proporcionar un pedido.")

    if not getattr(order, "pk", None):
        raise ValidationError(
            "El pedido debe existir en base de datos antes de resolver su alcance."
        )

    if not getattr(order, "company_id", None):
        raise ValidationError("El pedido no tiene empresa asignada.")

    return order


def _append_unique_warehouse(result, warehouse, *, company_id):
    if warehouse is None or not getattr(warehouse, "pk", None):
        return

    if getattr(warehouse, "company_id", None) != company_id:
        raise ValidationError(
            "Se detectó una bodega de otra empresa en el alcance del pedido."
        )

    if all(existing.pk != warehouse.pk for existing in result):
        result.append(warehouse)


def existing_order_authorization_warehouses(order):
    """
    Devuelve las bodegas YA asociadas al pedido sin modificar nada.

    Incluye fulfillments cancelados y pickings legacy porque una recuperación
    puede reutilizarlos. No reactiva estados ni crea asignaciones.
    """
    order = _validate_order(order)
    warehouses = []

    fulfillments = (
        OrderFulfillment.objects.filter(
            company_id=order.company_id,
            order_id=order.pk,
        )
        .select_related("warehouse")
        .order_by("sequence", "id")
    )

    for fulfillment in fulfillments:
        _append_unique_warehouse(
            warehouses,
            fulfillment.warehouse,
            company_id=order.company_id,
        )

    pickings = (
        PickingOrder.objects.filter(
            company_id=order.company_id,
            order_id=order.pk,
            warehouse_id__isnull=False,
        )
        .select_related("warehouse")
        .order_by("id")
    )

    for picking in pickings:
        _append_unique_warehouse(
            warehouses,
            picking.warehouse,
            company_id=order.company_id,
        )

    warehouses.sort(key=lambda warehouse: warehouse.pk)
    return tuple(warehouses)


def resolve_order_authorization_warehouses(
    order,
    *,
    plan_if_missing=True,
    fallback_to_company=False,
):
    """
    Resuelve el alcance de bodega para autorizar una acción del pedido.

    Orden seguro:
    1. Bodegas históricas/persistidas del pedido.
    2. Si no existen y plan_if_missing=True, simulación NUEVA de fulfillment.
       Se llama preview_fulfillment_plan únicamente cuando no existen
       fulfillments/pickings, evitando su rama histórica que puede reactivar
       fulfillments cancelados.
    3. Si la planificación no puede resolverse y fallback_to_company=True,
       usa todas las bodegas activas de la empresa como alcance conservador.

    Esta función no crea fulfillments, no reserva stock y no cambia estados.
    """
    order = _validate_order(order)

    existing = existing_order_authorization_warehouses(order)
    if existing:
        return existing

    planning_error = None

    if plan_if_missing:
        from warehouse.services.fulfillment_planning import (
            preview_fulfillment_plan,
        )

        try:
            plan = preview_fulfillment_plan(order)
        except ValidationError as exc:
            planning_error = exc
        else:
            warehouses = []

            for group in list(plan.get("groups") or []):
                _append_unique_warehouse(
                    warehouses,
                    group.get("warehouse"),
                    company_id=order.company_id,
                )

            if warehouses:
                warehouses.sort(key=lambda warehouse: warehouse.pk)
                return tuple(warehouses)

    if fallback_to_company:
        company_warehouses = tuple(
            Warehouse.objects.filter(
                company_id=order.company_id,
                is_active=True,
            ).order_by("id")
        )

        if company_warehouses:
            return company_warehouses

        raise ValidationError(
            "La empresa no tiene bodegas activas para determinar el alcance "
            "de autorización de este pedido."
        )

    if planning_error is not None:
        raise planning_error

    raise ValidationError(
        "No fue posible determinar las bodegas afectadas por este pedido."
    )


def _order_is_paid(order):
    """
    True si el pedido ya esta marcado como pagado (status == "paid").

    Se usa para decidir si una eliminacion de linea, cuando el pedido
    conserva otros productos, puede quedarse solo con el alcance
    prospectivo (bodegas que el pedido sigue necesitando) en vez de exigir
    tambien la bodega del producto que se elimina. Antes de que el pedido
    quede pagado (draft/pending), cualquier vendedor con permiso de
    ventas puede corregir un error de captura sin importar la bodega del
    producto eliminado -- solo debe tener acceso a las bodegas de lo que
    sigue en el pedido. En cuanto el pedido esta pagado ("ya paso a
    finanzas"), se vuelve al comportamiento conservador: se exige
    autoridad sobre TODAS las bodegas del pedido para cualquier cambio.
    """
    return order.status == "paid"


def resolve_order_mutation_authorization_warehouses(
    order,
    *,
    mutation,
    order_item_id=None,
    variant=None,
    commercial_quantity=None,
):
    """
    Resuelve las bodegas afectadas por una mutacion hipotetica
    de las lineas comerciales de un pedido.

    Es exclusivamente de lectura.

    Combina:
    - alcance actual/historico;
    - alcance prospectivo despues de add/update/delete.

    No sustituye resolve_order_authorization_warehouses().
    No persiste lineas, fulfillments, pickings, reservas ni stock.
    """

    from decimal import Decimal, InvalidOperation

    from warehouse.services import fulfillment_planning

    order = _validate_order(order)

    mutation = str(mutation or "").strip().lower()

    if mutation not in {
        "add",
        "update",
        "delete",
    }:
        raise ValidationError("La mutacion de pedido solicitada no es valida.")

    # --------------------------------------------------------
    # Validacion especifica para ADD
    # --------------------------------------------------------

    if mutation == "add":

        if variant is None:
            raise ValidationError("Debes indicar el SKU que se desea agregar.")

        if (
            getattr(
                variant,
                "company_id",
                None,
            )
            != order.company_id
        ):
            raise ValidationError("El SKU solicitado pertenece a otra empresa.")

        if getattr(
            variant,
            "is_bundle",
            False,
        ):
            raise ValidationError(
                "Los paquetes virtuales todavia no pueden "
                "planificarse prospectivamente."
            )

    # --------------------------------------------------------
    # Cantidad comercial
    # --------------------------------------------------------

    normalized_quantity = None

    if mutation in {
        "add",
        "update",
    }:

        try:
            normalized_quantity = Decimal(str(commercial_quantity))

        except (
            InvalidOperation,
            TypeError,
            ValueError,
        ) as exc:
            raise ValidationError(
                "La cantidad comercial solicitada no es valida."
            ) from exc

        if not normalized_quantity.is_finite() or normalized_quantity <= 0:
            raise ValidationError(
                "La cantidad comercial solicitada debe ser mayor a cero."
            )

    # --------------------------------------------------------
    # Requirements actuales
    # --------------------------------------------------------

    try:
        loaded_requirements = fulfillment_planning._load_requirements(order)

    except ValidationError:

        if mutation != "add":
            raise

        item_manager = getattr(
            order,
            "items",
            None,
        )

        if item_manager is None or not hasattr(item_manager, "exists"):
            raise

        if item_manager.exists():
            raise

        loaded_requirements = []

    requirements = [dict(requirement) for requirement in loaded_requirements]

    # --------------------------------------------------------
    # Validar item para UPDATE / DELETE
    # --------------------------------------------------------

    target_index = None

    if mutation in {
        "update",
        "delete",
    }:

        try:
            normalized_item_id = int(order_item_id)

        except (
            TypeError,
            ValueError,
        ) as exc:
            raise ValidationError(
                "La linea del pedido solicitada no es valida."
            ) from exc

        for index, requirement in enumerate(requirements):

            if int(requirement["order_item_id"]) == normalized_item_id:

                target_index = index
                break

        if target_index is None:
            raise ValidationError("La linea solicitada no pertenece al pedido.")

    # --------------------------------------------------------
    # Alcance actual
    # --------------------------------------------------------

    if requirements:

        current_warehouses = resolve_order_authorization_warehouses(
            order,
            plan_if_missing=True,
            fallback_to_company=True,
        )

    else:

        current_warehouses = ()

    # --------------------------------------------------------
    # Aplicar mutacion SOLO EN MEMORIA
    # --------------------------------------------------------

    if mutation == "add":

        matching_index = None

        for index, requirement in enumerate(requirements):

            if requirement["variant_id"] == variant.id:
                matching_index = index
                break

        if matching_index is not None:

            requirement = dict(requirements[matching_index])

            new_commercial_quantity = (
                Decimal(str(requirement["commercial_quantity"])) + normalized_quantity
            )

            internal_quantity = (
                fulfillment_planning.convert_order_item_quantity_to_internal(
                    variant=variant,
                    quantity=new_commercial_quantity,
                )
            )

            requirement["commercial_quantity"] = new_commercial_quantity

            requirement["internal_quantity"] = int(internal_quantity)

            requirements[matching_index] = requirement

        else:

            existing_ids = [
                int(requirement["order_item_id"]) for requirement in requirements
            ]

            synthetic_item_id = (
                min(
                    [
                        0,
                        *existing_ids,
                    ]
                )
                - 1
            )

            internal_quantity = (
                fulfillment_planning.convert_order_item_quantity_to_internal(
                    variant=variant,
                    quantity=normalized_quantity,
                )
            )

            requirements.append(
                {
                    "order_item": None,
                    "order_item_id": synthetic_item_id,
                    "variant": variant,
                    "variant_id": variant.id,
                    "commercial_quantity": normalized_quantity,
                    "internal_quantity": int(internal_quantity),
                }
            )

    elif mutation == "update":

        requirement = dict(requirements[target_index])

        target_variant = requirement["variant"]

        if (
            getattr(
                target_variant,
                "company_id",
                None,
            )
            != order.company_id
        ):
            raise ValidationError("El SKU de la linea pertenece a otra empresa.")

        internal_quantity = (
            fulfillment_planning.convert_order_item_quantity_to_internal(
                variant=target_variant,
                quantity=normalized_quantity,
            )
        )

        requirement["commercial_quantity"] = normalized_quantity

        requirement["internal_quantity"] = int(internal_quantity)

        requirements[target_index] = requirement

    else:

        del requirements[target_index]

    # --------------------------------------------------------
    # DELETE puede dejar pedido vacio
    #
    # Si el pedido queda vacio, SIEMPRE se conserva el alcance historico
    # completo (current_warehouses), sin importar si el pedido esta pagado
    # o no. No hay relajacion posible aqui: no queda un plan prospectivo
    # con el que comparar, asi que el alcance conservador es el unico
    # disponible.
    # --------------------------------------------------------

    if not requirements:

        return tuple(current_warehouses)

    # --------------------------------------------------------
    # Plan prospectivo
    # --------------------------------------------------------

    try:

        prospective_plan = fulfillment_planning._build_new_plan(
            order=order,
            requirements=requirements,
            lock_stock=False,
        )

    except ValidationError:

        # Fallback conservador:
        # si el planner no puede resolver el futuro con certeza,
        # exigimos autoridad sobre todas las bodegas activas.
        fallback = tuple(
            Warehouse.objects.filter(
                company_id=order.company_id,
                is_active=True,
            ).order_by("id")
        )

        if not fallback:
            raise

        prospective_warehouses = fallback

    else:

        prospective_warehouses = []

        for group in list(prospective_plan.get("groups") or []):

            warehouse = group.get("warehouse")

            _append_unique_warehouse(
                prospective_warehouses,
                warehouse,
                company_id=order.company_id,
            )

        if not prospective_warehouses:
            raise ValidationError(
                "No fue posible determinar las bodegas "
                "del pedido despues de la mutacion."
            )

    # --------------------------------------------------------
    # Relajacion para DELETE en pedido NO pagado
    #
    # Si el pedido sigue teniendo productos despues de esta eliminacion
    # (no quedo vacio, ya se filtro arriba) y AUN NO esta pagado ("no ha
    # pasado a finanzas"), no exigimos autoridad sobre las bodegas
    # historicas completas del pedido -- basta con las bodegas que el
    # pedido sigue necesitando despues de quitar esta linea
    # (prospective_warehouses). En cuanto el pedido esta pagado, se
    # conserva el comportamiento conservador de siempre (union completa,
    # mas abajo) -- en ese punto, corregir una linea que toca una bodega
    # ajena requiere que lo haga alguien con visibilidad completa de la
    # empresa (sales.supervise), no cualquier vendedor.
    # --------------------------------------------------------

    if mutation == "delete" and not _order_is_paid(order):
        return tuple(prospective_warehouses)

    # --------------------------------------------------------
    # UNION ACTUAL + FUTURO
    # --------------------------------------------------------

    result = []

    combined = tuple(current_warehouses) + tuple(prospective_warehouses)

    for warehouse in combined:

        _append_unique_warehouse(
            result,
            warehouse,
            company_id=order.company_id,
        )

    result.sort(key=lambda warehouse: warehouse.pk)

    return tuple(result)
