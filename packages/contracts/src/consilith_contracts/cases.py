"""Case packs: lanes, input grammar and spec fields for each studio.

A case is data, so new presets plug in without core changes. Plugins describe default roles;
authenticated participants claim lanes independently of their names.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Self, cast

from pydantic import AfterValidator, Field, model_validator

from .base import Contract
from .units import UnitError, check, convert

_RESERVED_IDENTIFIERS = frozenset(
    {
        ".",
        "..",
        "__proto__",
        "prototype",
        "constructor",
        "__defineGetter__",
        "__defineSetter__",
        "__lookupGetter__",
        "__lookupSetter__",
        "hasOwnProperty",
        "isPrototypeOf",
        "propertyIsEnumerable",
        "toLocaleString",
        "toString",
        "valueOf",
    }
)


def validate_identifier(value: object) -> str:
    """Portable pointer/map identifier; human-readable labels have no such restriction."""
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value) is None
        or value in _RESERVED_IDENTIFIERS
    ):
        raise ValueError("identifier must use letters, digits, _, . or - and not be a reserved key")
    return value


Identifier = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$"),
    AfterValidator(validate_identifier),
]


class FieldDef(Contract):
    """One spec value inside a lane. Numbers are stored as ``{"v": float, "unit": str}``."""

    name: Identifier
    kind: Literal["number", "integer", "text", "choice", "list", "object"]
    unit: str = ""  # the dimension a number must have, e.g. "mm"
    min: float | None = None
    max: float | None = None
    choices: list[str] = Field(default_factory=list[str])
    required: bool = False
    label: str = ""


class LaneDef(Contract):
    id: Identifier
    label: str
    default_role: str = Field(min_length=1)
    fields: list[FieldDef]

    @model_validator(mode="after")
    def _unique_fields(self) -> Self:
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("duplicate field identifiers in a lane")
        return self


class CaseDef(Contract):
    id: Identifier
    label: str
    lanes: list[LaneDef]
    grammar: str  # what the studio accepts (FR-LOOP-11)
    inputs: list[str]  # intake kinds routed here

    @model_validator(mode="after")
    def _unique_lanes(self) -> Self:
        names = self.lane_ids()
        if len(names) != len(set(names)):
            raise ValueError("duplicate lane identifiers in a case")
        return self

    def lane(self, lane_id: str) -> LaneDef | None:
        return next((ln for ln in self.lanes if ln.id == lane_id), None)

    def lane_ids(self) -> list[str]:
        return [ln.id for ln in self.lanes]


class SpecError(ValueError):
    """A spec value outside the case's grammar. ``field`` names the path that failed."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


CASES: dict[str, CaseDef] = {}


def validate_lane(case: CaseDef, lane_id: str, data: Any, *, require: bool = False) -> None:
    """Check one lane's subtree against its field definitions. Raises SpecError."""
    lane = case.lane(lane_id)
    base = f"/twin/{case.id}/{lane_id}"
    if lane is None:
        raise SpecError(base, "unknown lane")
    if not isinstance(data, dict):
        raise SpecError(base, "a lane holds an object")
    data = cast(dict[str, Any], data)
    known = {f.name: f for f in lane.fields}
    for key in data:
        if key not in known:
            raise SpecError(f"{base}/{key}", "not a field of this lane")
    for f in lane.fields:
        path = f"{base}/{f.name}"
        if f.name not in data:
            if require and f.required:
                raise SpecError(path, "required")
            continue
        _validate_field(f, path, data[f.name])


def _validate_field(f: FieldDef, path: str, value: Any) -> None:
    match f.kind:
        case "number" | "integer":
            if not isinstance(value, dict):
                raise SpecError(path, 'a number is stored as {"v": number, "unit": string}')
            num = cast(dict[str, Any], value)
            if set(num) - {"v", "unit"} or "v" not in num:
                raise SpecError(path, 'a number is stored as {"v": number, "unit": string}')
            v: Any = num["v"]
            if isinstance(v, bool) or not isinstance(v, int | float):
                raise SpecError(path, "v must be a number")
            if f.kind == "integer" and float(v) != int(v):
                raise SpecError(path, "must be an integer")
            unit: Any = num.get("unit", "")
            if not isinstance(unit, str):
                raise SpecError(path, "unit must be a string")
            try:
                check(unit, f.unit)
            except UnitError as exc:
                raise SpecError(path, str(exc)) from exc
            v_cmp = convert(float(v), unit, f.unit) if f.unit else float(v)
            if f.min is not None and v_cmp < f.min:
                raise SpecError(path, f"below the studio's range (min {f.min} {f.unit})")
            if f.max is not None and v_cmp > f.max:
                raise SpecError(path, f"above the studio's range (max {f.max} {f.unit})")
        case "text":
            if not isinstance(value, str):
                raise SpecError(path, "must be text")
        case "choice":
            if value not in f.choices:
                raise SpecError(path, f"must be one of {', '.join(f.choices)}")
        case "list":
            if not isinstance(value, list):
                raise SpecError(path, "must be a list")
        case "object":
            if not isinstance(value, dict):
                raise SpecError(path, "must be an object")
