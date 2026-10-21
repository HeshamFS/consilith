"""Neutral analysis compatibility port. The selected plugin owns the language and mathematics."""

from __future__ import annotations

from dataclasses import dataclass

from .extensions import extension


class AnalysisError(ValueError):
    """A selected analysis implementation refused or failed to evaluate the request."""


@dataclass(frozen=True)
class Value:
    magnitude: float
    unit: str = ""


def validate(source: str) -> None:
    try:
        extension("analysis_validators", "default")(source)
    except Exception as exc:
        raise AnalysisError(str(exc)) from exc


def run_isolated(
    source: str, results: dict[str, tuple[float, str]], timeout_s: float = 10.0
) -> tuple[Value, list[str]]:
    try:
        engine = extension("analysis_engines", "default")
        value, unit, used = engine(source, results, timeout_s)
        return Value(float(value), str(unit)), list(used)
    except Exception as exc:
        raise AnalysisError(str(exc)) from exc


def evaluate(source: str, results: dict[str, tuple[float, str]]) -> tuple[Value, list[str]]:
    return run_isolated(source, results)


check = validate
run = evaluate
