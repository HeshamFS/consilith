"""Generic evidence policy and backwards-compatible extension lookup."""

from typing import Any

from consilith_contracts import Stamp
from consilith_contracts.execution import Finding
from consilith_contracts.extensions import extension


def stamp_for(findings: list[Finding]) -> Stamp:
    failed = [f for f in findings if f.check.outcome == "fail"]
    if not findings or any(f.severity == "block" for f in failed):
        return Stamp.BLOCKED
    if failed or any(f.check.outcome in ("warn", "skip") for f in findings):
        return Stamp.WARNING
    return Stamp.VERIFIED


def __getattr__(name: str) -> Any:
    if name.startswith("_"):
        raise AttributeError(name)
    try:
        return extension("checker_exports", name)
    except LookupError as exc:
        raise AttributeError(name) from exc
