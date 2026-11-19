"""Núcleo determinista de matemática financiera multibodega.

Todas las cantidades monetarias internas se expresan como centavos enteros.
Este módulo es puro: no consulta la base de datos ni modifica registros.
"""

from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from typing import Hashable, Iterable, Mapping


QUALITY_REAL = "real"
QUALITY_ATTRIBUTED = "attributed"
QUALITY_ESTIMATED = "estimated"
QUALITY_ORDER = {
    QUALITY_REAL: 0,
    QUALITY_ATTRIBUTED: 1,
    QUALITY_ESTIMATED: 2,
}


def _require_cents(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} debe ser un entero de centavos.")
    return value


def _require_decimal(value, name):
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} debe ser Decimal; float no está permitido.")
    if not value.is_finite():
        raise ValueError(f"{name} debe ser finito.")
    return value


def money_to_cents(value: Decimal) -> int:
    """Convierte Decimal a centavos usando ROUND_HALF_UP explícito."""
    value = _require_decimal(value, "value")
    return int((value * Decimal("100")).to_integral_value(rounding=ROUND_HALF_UP))


def cents_to_money(value: int) -> Decimal:
    """Convierte centavos enteros a Decimal con exactamente dos decimales."""
    value = _require_cents(value, "value")
    return (Decimal(value) / Decimal("100")).quantize(Decimal("0.01"))


def allocate_largest_remainder(
    *,
    total_cents: int,
    weights: Iterable[tuple[Hashable, Decimal]],
) -> dict[Hashable, int]:
    """Distribuye centavos exactamente mediante residuo mayor.

    Los empates se resuelven por la representación textual estable de la clave,
    por lo que el resultado no depende del orden de entrada.
    """
    total_cents = _require_cents(total_cents, "total_cents")
    entries = list(weights)
    if not entries:
        if total_cents:
            raise ValueError("No se puede distribuir un total sin destinos.")
        return {}

    normalized = []
    seen = set()
    for key, weight in entries:
        try:
            duplicated = key in seen
        except TypeError as exc:
            raise TypeError("Las claves de distribución deben ser hashables.") from exc
        if duplicated:
            raise ValueError(f"Clave de distribución duplicada: {key!r}.")
        seen.add(key)
        weight = _require_decimal(weight, f"weight[{key!r}]")
        if weight < 0:
            raise ValueError("Los pesos de distribución no pueden ser negativos.")
        normalized.append((key, weight))

    weight_total = sum((weight for _, weight in normalized), Decimal("0"))
    if weight_total == 0:
        if total_cents:
            raise ValueError("No se puede distribuir un total con pesos en cero.")
        return {key: 0 for key, _ in sorted(normalized, key=lambda item: str(item[0]))}

    sign = -1 if total_cents < 0 else 1
    magnitude = abs(total_cents)
    allocations = {}
    remainders = []

    for key, weight in normalized:
        exact = Decimal(magnitude) * weight / weight_total
        floor_value = int(exact.to_integral_value(rounding=ROUND_FLOOR))
        allocations[key] = floor_value
        remainders.append((exact - Decimal(floor_value), str(key), key))

    missing = magnitude - sum(allocations.values())
    for _, _, key in sorted(remainders, key=lambda item: (-item[0], item[1]))[:missing]:
        allocations[key] += 1

    return {
        key: sign * allocations[key]
        for key, _ in sorted(normalized, key=lambda item: str(item[0]))
    }


def derive_warehouse_row(
    *,
    subtotal_cents: int,
    discount_cents: int,
    tax_cents: int,
    shipping_cents: int,
    cost_cents: int,
    legacy_difference_cents: int = 0,
    rounding_residual_cents: int = 0,
) -> dict[str, int | bool]:
    """Deriva total y utilidad desde los componentes de una misma fila.

    El residuo de redondeo se conserva para auditoría, pero no se vuelve a
    sumar: ya está contenido en el componente al que fue asignado.
    """
    values = {
        "subtotal_cents": subtotal_cents,
        "discount_cents": discount_cents,
        "tax_cents": tax_cents,
        "shipping_cents": shipping_cents,
        "cost_cents": cost_cents,
        "legacy_difference_cents": legacy_difference_cents,
        "rounding_residual_cents": rounding_residual_cents,
    }
    for name, value in values.items():
        _require_cents(value, name)

    total_cents = (
        subtotal_cents
        - discount_cents
        + tax_cents
        + shipping_cents
        + legacy_difference_cents
    )
    gross_profit_cents = subtotal_cents - discount_cents - cost_cents

    return {
        **values,
        "total_cents": total_cents,
        "gross_profit_cents": gross_profit_cents,
        "has_legacy_inconsistency": legacy_difference_cents != 0,
    }


def dominant_quality(*qualities: str) -> str:
    """Devuelve la calidad menos confiable: real < atribuida < estimada."""
    if not qualities:
        raise ValueError("Se requiere al menos una calidad.")
    invalid = [quality for quality in qualities if quality not in QUALITY_ORDER]
    if invalid:
        raise ValueError(f"Calidad financiera no válida: {invalid[0]!r}.")
    return max(qualities, key=QUALITY_ORDER.__getitem__)


def quality_for_value(
    *,
    amount_quality: str,
    confirmation_date_is_inferred: bool = False,
    estimate_reasons: Iterable[str] = (),
) -> dict[str, object]:
    """Propaga calidad y unifica fecha inferida bajo la etiqueta Estimado."""
    dominant_quality(amount_quality)
    reasons = list(dict.fromkeys(str(reason) for reason in estimate_reasons if reason))
    if confirmation_date_is_inferred:
        reasons.append("inferred_confirmation_date")
    reasons = list(dict.fromkeys(reasons))
    quality = dominant_quality(
        amount_quality,
        QUALITY_ESTIMATED if reasons else amount_quality,
    )
    return {
        "quality_label": quality,
        "is_estimated": quality == QUALITY_ESTIMATED,
        "estimate_reasons": reasons,
    }


def reconcile_scope(
    *,
    consolidated_cents: int,
    warehouse_cents: Mapping[Hashable, int],
    unassigned_cents: int,
) -> dict[str, int | bool]:
    """Detecta diferencias; nunca corrige ni maquilla los valores recibidos."""
    consolidated_cents = _require_cents(consolidated_cents, "consolidated_cents")
    unassigned_cents = _require_cents(unassigned_cents, "unassigned_cents")
    assigned_cents = 0
    for key, value in warehouse_cents.items():
        assigned_cents += _require_cents(value, f"warehouse_cents[{key!r}]")
    accounted_cents = assigned_cents + unassigned_cents
    difference_cents = consolidated_cents - accounted_cents
    return {
        "consolidated_cents": consolidated_cents,
        "assigned_cents": assigned_cents,
        "unassigned_cents": unassigned_cents,
        "accounted_cents": accounted_cents,
        "difference_cents": difference_cents,
        "is_reconciled": difference_cents == 0,
    }
