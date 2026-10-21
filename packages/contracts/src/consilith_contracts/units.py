"""Units: every value carries one, and pint checks it before display (FR-RG-2)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import pint


class UnitError(ValueError):
    """A unit string pint cannot parse, or two units of different dimensions."""


@lru_cache(maxsize=1)
def registry() -> pint.UnitRegistry:
    return pint.UnitRegistry()


def parse(unit: str) -> pint.Unit:
    if unit == "":
        return registry().dimensionless
    try:
        return registry().Unit(unit)
    except (pint.errors.UndefinedUnitError, pint.errors.DefinitionSyntaxError, ValueError) as exc:
        raise UnitError(f"unknown unit {unit!r}") from exc
    except Exception as exc:
        raise UnitError(f"unparseable unit {unit!r}") from exc


def dimensionality(unit: str) -> str:
    return str(parse(unit).dimensionality)


def same_dimension(a: str, b: str) -> bool:
    return parse(a).dimensionality == parse(b).dimensionality


def convert(value: float, unit: str, to: str) -> float:
    if not same_dimension(unit, to):
        raise UnitError(f"cannot convert {unit!r} to {to!r}: different dimensions")
    q: Any = registry().Quantity(value, parse(unit))
    return float(q.to(parse(to)).magnitude)


def check(unit: str, expected_dimension_of: str) -> None:
    """Raise UnitError unless ``unit`` has the dimension of ``expected_dimension_of``."""
    if not same_dimension(unit, expected_dimension_of):
        raise UnitError(
            f"unit {unit!r} has dimension {dimensionality(unit)}, "
            f"expected {dimensionality(expected_dimension_of)}"
        )
