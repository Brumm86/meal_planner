"""Pure shopping-list arithmetic (no Home Assistant, no I/O).

A *position* is identified by ``(ingredient_id, dimension)``. Units of one
dimension (g/kg, ml/l) are added together in the dimension's base unit; every
other unit is its own dimension, so "2 Stück" and "300 g" of one ingredient
stay two positions. Dimension ``""`` is the "nach Bedarf" position of an
ingredient (no quantity, value ``None``).
"""

from __future__ import annotations

from typing import Any

from .const import UNITS

Key = tuple[str, str]
Amounts = dict[Key, float | None]

_EPSILON = 1e-6
NO_DIMENSION = ""

# dimension -> base unit and, if the dimension has one, the larger unit that is
# used for display once the amount reaches 1000 base units.
_BASE_UNIT: dict[str, str] = {}
for _unit, (_dimension, _factor) in UNITS.items():
    if _factor == 1.0:
        _BASE_UNIT[_dimension] = _unit
_LARGE_UNIT = {"mass": "kg", "volume": "l"}


def to_base(amount: float | None, unit: str | None) -> tuple[str, float | None]:
    """Convert ``amount`` ``unit`` to ``(dimension, amount in base unit)``."""
    if amount is None or unit is None:
        return NO_DIMENSION, None
    dimension, factor = UNITS[unit]
    return dimension, round(amount * factor, 3)


def from_base(dimension: str, amount: float | None) -> tuple[str | None, float | None]:
    """Turn a base amount into a readable ``(unit, amount)``: 1500 g -> 1.5 kg."""
    if dimension == NO_DIMENSION or amount is None:
        return None, None
    unit = _BASE_UNIT[dimension]
    large = _LARGE_UNIT.get(dimension)
    if large is not None and amount >= 1000:
        return large, round(amount / UNITS[large][1], 3)
    return unit, round(amount, 3)


def needed_for_entry(
    entry: dict[str, Any] | None,
    recipe: dict[str, Any] | None,
    household_size: int,
) -> Amounts:
    """What one plan entry needs: the recipe's lines scaled to its portions."""
    if entry is None or recipe is None or entry.get("kind") != "recipe":
        return {}
    portions = entry.get("portions") or household_size
    factor = portions / recipe["base_portions"]
    needed: Amounts = {}
    for line in recipe.get("ingredients", []):
        dimension, base = to_base(line.get("amount"), line.get("unit"))
        key = (line["ingredient_id"], dimension)
        if base is None:
            needed.setdefault(key, None)
        else:
            needed[key] = round((needed.get(key) or 0.0) + base * factor, 3)
    return needed


def diff(needed: Amounts, transferred: Amounts) -> tuple[Amounts, Amounts]:
    """Split needed vs. already-transferred into ``(to_add, no_longer_needed)``.

    Only the positive difference is to be added; whatever was transferred but
    is now needed less (or not at all) is reported separately and never
    removed from Bring.
    """
    to_add: Amounts = {}
    surplus: Amounts = {}
    for key, value in needed.items():
        if value is None:
            if key not in transferred:
                to_add[key] = None
        else:
            missing = round(value - (transferred.get(key) or 0.0), 3)
            if missing > _EPSILON:
                to_add[key] = missing
    for key, value in transferred.items():
        if value is None:
            if key not in needed:
                surplus[key] = None
        else:
            extra = round(value - (needed.get(key) or 0.0), 3)
            if extra > _EPSILON:
                surplus[key] = extra
    return to_add, surplus


def raised(needed: Amounts, transferred: Amounts) -> Amounts:
    """The new record after a transfer: per position the larger of both."""
    result = dict(transferred)
    for key, value in needed.items():
        if key not in result:
            result[key] = value
        elif value is not None and (result[key] is None or value > result[key]):
            result[key] = value
    return result


def add_into(target: Amounts, source: Amounts) -> None:
    """Sum ``source`` into ``target`` (None stays None = 'nach Bedarf')."""
    for key, value in source.items():
        if value is None:
            target.setdefault(key, None)
        else:
            target[key] = round((target.get(key) or 0.0) + value, 3)


def lines_to_amounts(lines: list[dict[str, Any]]) -> Amounts:
    """Stored transfer lines -> amounts."""
    return {(ln["ingredient_id"], ln["dimension"]): ln["amount"] for ln in lines}


def amounts_to_lines(amounts: Amounts) -> list[dict[str, Any]]:
    """Amounts -> stored transfer lines."""
    return [
        {"ingredient_id": key[0], "dimension": key[1], "amount": value}
        for key, value in sorted(amounts.items(), key=lambda kv: (kv[0][0], kv[0][1]))
    ]


def format_quantity(amount: float | None, unit: str | None) -> str:
    """'400 g', '1,5 kg'; empty for 'nach Bedarf'."""
    if amount is None or unit is None:
        return ""
    text = f"{round(amount, 3):g}".replace(".", ",")
    return f"{text} {unit}"
