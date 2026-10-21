"""Compatibility facade for versioned deterministic extension computations."""

from typing import Any

from consilith_contracts.extensions import all_extensions, extension


def evaluate(
    name: str, inputs: dict[str, float], context: dict[str, Any] | None = None
) -> list[tuple[str, float, str]]:
    return extension("formulas", name)(inputs, context or {})


def __getattr__(name: str) -> Any:
    if name == "FORMULAS":
        return all_extensions("formulas")
    raise AttributeError(name)


def evaluate_pinned(
    entry_points: list[str],
    pins: list[dict[str, Any]],
    name: str,
    inputs: dict[str, float],
    context: dict[str, Any],
) -> list[tuple[str, float, str]]:
    from consilith_contracts.extensions import PackagePin, load_extensions, package_for

    expected = [PackagePin.model_validate(pin) for pin in pins]
    load_extensions(entry_points, expected=expected)
    if expected and package_for("formulas", name) not in expected:
        raise ValueError("formula provider is not part of the pinned execution environment")
    return evaluate(name, inputs, context)
