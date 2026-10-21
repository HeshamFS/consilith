"""Machine-checkable lessons from a person's own words (FR-MEM-1/2).

"under 5 mm is hard to machine" on a declined fillet_r change becomes
{"param": "fillet_r", "min": 5.0, "unit": "mm"}. The number is the person's; this is parsing,
not a model producing a value.
"""

from __future__ import annotations

import re
from typing import Any

from consilith_contracts.units import UnitError, convert, parse

LESSON_MIN = re.compile(
    r"(?:under|below|less than|smaller than|thinner than)\s+(\d+(?:\.\d+)?)\s*"
    r"([A-Za-zµμ°][A-Za-z0-9µμ°/^*_.-]*)",
    re.I,
)
LESSON_MAX = re.compile(
    r"(?:over|above|more than|larger than|thicker than)\s+(\d+(?:\.\d+)?)\s*"
    r"([A-Za-zµμ°][A-Za-z0-9µμ°/^*_.-]*)",
    re.I,
)


def rule_from_reason(reason: str, param: str) -> dict[str, Any] | None:
    for pattern, bound in ((LESSON_MIN, "min"), (LESSON_MAX, "max")):
        if m := pattern.search(reason):
            unit = m.group(2).replace("µ", "u").replace("μ", "u")
            try:
                parse(unit)
            except UnitError:
                continue
            return {"param": param, bound: float(m.group(1)), "unit": unit}
    return None


def rule_blocks(rule: dict[str, Any], param: str, value: float, unit: str) -> bool:
    """Whether a lesson rule rules out ``param = value unit``."""
    if rule.get("param") != param:
        return False
    try:
        v = convert(value, unit, str(rule.get("unit", unit)))
    except UnitError:
        return False
    if "min" in rule and v < float(rule["min"]):
        return True
    return "max" in rule and v > float(rule["max"])
