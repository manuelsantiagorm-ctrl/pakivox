from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from subscriptions.models import Subscription

from accounts.warehouse_access import (
    ALL_WAREHOUSES_VALUE,
    SESSION_WAREHOUSE_KEY,
    get_user_company,
    user_has_company_wide_warehouse_access,
)
from inventory.models import Stock, Warehouse
from warehouse.models import WarehouseLocation

from .warehouse_forms import WarehouseForm


def _get_company_for_warehouse_management(user):
    company = get_user_company(user)

    if not company:
        raise PermissionDenied("Tu usuario no tiene una empresa activa asignada.")

    if not user_has_company_wide_warehouse_access(user):
        raise PermissionDenied(
            "Solo el dueño o administrador puede administrar bodegas."
        )

    return company


def _add_validation_error_to_form(form, error):
    if hasattr(error, "message_dict"):
        for field_name, error_messages in error.message_dict.items():
            form_field = field_name if field_name in form.fields else None

            for error_message in error_messages:
                form.add_error(
                    form_field,
                    error_message,
                )

        return

    for error_message in error.messages:
        form.add_error(
            None,
            error_message,
        )


def _get_warehouse_plan_limit(company, lock=False):
    """
    Obtiene el límite de bodegas contratado por la empresa.

    Si lock=True, bloquea la suscripción durante la transacción para
    impedir que dos solicitudes simultáneas excedan el límite.

    Las empresas antiguas que todavía no tengan una suscripción
    configurada conservan temporalmente su comportamiento actual.
    """
    subscriptions = Subscription.objects.select_related("plan")

    if lock:
        subscriptions = subscriptions.select_for_update()

    try:
        subscription = subscriptions.get(company=company)
    except Subscription.DoesNotExist:
        return None, None

    return subscription, subscription.plan.max_warehouses


def _apply_primary_warehouse_rules(
    company,
    warehouse,
    previous_is_primary=False,
):
    company_warehouses = Warehouse.objects.select_for_update().filter(company=company)

    now = timezone.now()

    if warehouse.is_primary:
        if not warehouse.is_active:
            raise ValidationError(
                {"is_primary": ("La bodega principal debe permanecer activa.")}
            )

        (
            company_warehouses.exclude(pk=warehouse.pk)
            .filter(is_primary=True)
            .update(
                is_primary=False,
                updated_at=now,
            )
        )

        return

    active_primary_exists = (
        company_warehouses.exclude(pk=warehouse.pk)
        .filter(
            is_primary=True,
            is_active=True,
        )
        .exists()
    )

    if active_primary_exists and not previous_is_primary:
        return

    replacement = (
        company_warehouses.exclude(pk=warehouse.pk)
        .filter(is_active=True)
        .order_by(
            "created_at",
            "id",
        )
        .first()
    )

    if replacement:
        company_warehouses.filter(
            is_primary=True,
        ).update(
            is_primary=False,
            updated_at=now,
        )

        replacement.is_primary = True

        replacement.save(
            update_fields=[
                "is_primary",
                "updated_at",
            ]
        )

        return

    if warehouse.is_active:
        warehouse.is_primary = True
        return

    raise ValidationError(
        {"is_active": ("Debe existir al menos una bodega principal activa.")}
    )


@login_required(login_url="/accounts/login/")
def warehouse_list(request):
    company = _get_company_for_warehouse_management(
        request.user,
    )

    warehouses = list(
        Warehouse.objects.filter(
            company=company,
        ).order_by(
            "-is_primary",
            "-is_active",
            "name",
            "id",
        )
    )

    warehouse_ids = [warehouse.id for warehouse in warehouses]

    stock_rows = (
        Stock.objects.filter(
            company=company,
            warehouse_id__in=warehouse_ids,
        )
        .values(
            "warehouse_id",
        )
        .annotate(
            products_count=Count(
                "variant_id",
                distinct=True,
            ),
            physical_total=Sum(
                "physical_stock",
            ),
            reserved_total=Sum(
                "reserved_stock",
            ),
            committed_total=Sum(
                "committed_stock",
            ),
        )
    )

    stock_map = {row["warehouse_id"]: row for row in stock_rows}

    location_rows = (
        WarehouseLocation.objects.filter(
            company=company,
            warehouse_id__in=warehouse_ids,
        )
        .values(
            "warehouse_id",
        )
        .annotate(
            locations_count=Count(
                "id",
            ),
        )
    )

    location_map = {
        row["warehouse_id"]: row["locations_count"] for row in location_rows
    }

    for warehouse in warehouses:
        warehouse_stock = stock_map.get(
            warehouse.id,
            {},
        )

        warehouse.products_count = (
            warehouse_stock.get(
                "products_count",
                0,
            )
            or 0
        )

        warehouse.physical_total = (
            warehouse_stock.get(
                "physical_total",
                0,
            )
            or 0
        )

        warehouse.reserved_total = (
            warehouse_stock.get(
                "reserved_total",
                0,
            )
            or 0
        )

        warehouse.committed_total = (
            warehouse_stock.get(
                "committed_total",
                0,
            )
            or 0
        )

        warehouse.available_total = (
            warehouse.physical_total
            - warehouse.reserved_total
            - warehouse.committed_total
        )

        warehouse.locations_count = location_map.get(
            warehouse.id,
            0,
        )

        warehouse.occupancy_percent = 0

        if warehouse.capacity_positions:
            warehouse.occupancy_percent = min(
                100,
                round(
                    (warehouse.locations_count / warehouse.capacity_positions) * 100,
                    1,
                ),
            )

    subscription, warehouse_limit = _get_warehouse_plan_limit(
        company=company,
        lock=False,
    )

    total_warehouses = len(warehouses)

    active_warehouses = sum(1 for warehouse in warehouses if warehouse.is_active)

    warehouse_limit_reached = (
        warehouse_limit is not None and total_warehouses >= warehouse_limit
    )

    return render(
        request,
        "companies/warehouse_list.html",
        {
            "company": company,
            "warehouses": warehouses,
            "total_warehouses": total_warehouses,
            "active_warehouses": active_warehouses,
            "subscription": subscription,
            "warehouse_limit": warehouse_limit,
            "warehouse_limit_reached": warehouse_limit_reached,
        },
    )


@login_required(login_url="/accounts/login/")
def warehouse_create(request):
    company = _get_company_for_warehouse_management(
        request.user,
    )

    subscription, warehouse_limit = _get_warehouse_plan_limit(
        company=company,
        lock=False,
    )

    current_warehouses = Warehouse.objects.filter(
        company=company,
    ).count()

    limit_reached = (
        warehouse_limit is not None and current_warehouses >= warehouse_limit
    )

    if request.method != "POST" and limit_reached:
        messages.warning(
            request,
            (
                f"Tu plan permite un máximo de {warehouse_limit} "
                f"{'bodega' if warehouse_limit == 1 else 'bodegas'}. "
                "Ya utilizaste toda la capacidad disponible. "
                "Actualiza tu plan para agregar otra bodega."
            ),
        )

        return redirect("/ceo/")

    form = WarehouseForm(
        request.POST or None,
        company=company,
    )

    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                locked_subscription, locked_limit = _get_warehouse_plan_limit(
                    company=company,
                    lock=True,
                )

                locked_warehouse_count = (
                    Warehouse.objects.select_for_update()
                    .filter(company=company)
                    .count()
                )

                if locked_limit is not None and locked_warehouse_count >= locked_limit:
                    raise ValidationError(
                        (
                            f"Tu plan permite un máximo de {locked_limit} "
                            f"{'bodega' if locked_limit == 1 else 'bodegas'}. "
                            "Actualiza tu plan para aumentar la capacidad."
                        )
                    )

                warehouse = form.save(
                    commit=False,
                )

                warehouse.company = company

                _apply_primary_warehouse_rules(
                    company=company,
                    warehouse=warehouse,
                    previous_is_primary=False,
                )

                warehouse.full_clean()
                warehouse.save()

        except ValidationError as error:
            _add_validation_error_to_form(
                form,
                error,
            )

        except IntegrityError:
            form.add_error(
                None,
                (
                    "No fue posible crear la bodega porque existe "
                    "otro registro con los mismos datos."
                ),
            )

        else:
            request.session[SESSION_WAREHOUSE_KEY] = warehouse.id
            request.session.modified = True

            messages.success(
                request,
                (f"La bodega {warehouse.name} fue creada " "y quedó seleccionada."),
            )

            return redirect("companies:warehouse_list")

    return render(
        request,
        "companies/warehouse_form.html",
        {
            "company": company,
            "form": form,
            "page_mode": "create",
            "page_title_text": "Nueva bodega",
            "submit_text": "Crear bodega",
            "warehouse": None,
            "subscription": subscription,
            "warehouse_limit": warehouse_limit,
            "current_warehouses": current_warehouses,
            "limit_reached": limit_reached,
        },
    )


@login_required(login_url="/accounts/login/")
def warehouse_update(request, warehouse_id):
    company = _get_company_for_warehouse_management(
        request.user,
    )

    warehouse = get_object_or_404(
        Warehouse,
        id=warehouse_id,
        company=company,
    )

    previous_is_primary = warehouse.is_primary

    form = WarehouseForm(
        request.POST or None,
        instance=warehouse,
        company=company,
    )

    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                Warehouse.objects.select_for_update().get(
                    id=warehouse.id,
                    company=company,
                )

                warehouse = form.save(
                    commit=False,
                )

                warehouse.company = company

                _apply_primary_warehouse_rules(
                    company=company,
                    warehouse=warehouse,
                    previous_is_primary=previous_is_primary,
                )

                warehouse.full_clean()
                warehouse.save()

        except ValidationError as error:
            _add_validation_error_to_form(
                form,
                error,
            )

        except IntegrityError:
            form.add_error(
                None,
                (
                    "No fue posible guardar los cambios porque existe "
                    "otro registro con los mismos datos."
                ),
            )

        else:
            selected_value = request.session.get(
                SESSION_WAREHOUSE_KEY,
            )

            if not warehouse.is_active and str(selected_value) == str(warehouse.id):
                request.session[SESSION_WAREHOUSE_KEY] = ALL_WAREHOUSES_VALUE

                request.session.modified = True

            messages.success(
                request,
                f"La bodega {warehouse.name} fue actualizada.",
            )

            return redirect("companies:warehouse_list")

    return render(
        request,
        "companies/warehouse_form.html",
        {
            "company": company,
            "form": form,
            "page_mode": "update",
            "page_title_text": "Editar bodega",
            "submit_text": "Guardar cambios",
            "warehouse": warehouse,
        },
    )


@login_required(login_url="/accounts/login/")
@require_POST
def warehouse_toggle_active(request, warehouse_id):
    company = _get_company_for_warehouse_management(
        request.user,
    )

    try:
        with transaction.atomic():
            warehouse = get_object_or_404(
                Warehouse.objects.select_for_update(),
                id=warehouse_id,
                company=company,
            )

            was_primary = warehouse.is_primary

            warehouse.is_active = not warehouse.is_active

            if not warehouse.is_active:
                warehouse.is_primary = False

            _apply_primary_warehouse_rules(
                company=company,
                warehouse=warehouse,
                previous_is_primary=was_primary,
            )

            warehouse.full_clean()
            warehouse.save()

    except ValidationError as error:
        message = next(
            iter(error.messages),
            "No fue posible cambiar el estado de la bodega.",
        )

        messages.error(
            request,
            message,
        )

        return redirect("companies:warehouse_list")

    selected_value = request.session.get(
        SESSION_WAREHOUSE_KEY,
    )

    if not warehouse.is_active and str(selected_value) == str(warehouse.id):
        request.session[SESSION_WAREHOUSE_KEY] = ALL_WAREHOUSES_VALUE

        request.session.modified = True

    if warehouse.is_active:
        messages.success(
            request,
            f"La bodega {warehouse.name} fue activada.",
        )
    else:
        messages.warning(
            request,
            f"La bodega {warehouse.name} fue desactivada.",
        )

    return redirect("companies:warehouse_list")
