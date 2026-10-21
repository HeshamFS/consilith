"""Atomic data and remote call registry. Contracts never import plugin implementations."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal, cast

from pydantic import Field

from .base import Contract
from .cases import CASES, CaseDef, validate_identifier
from .plugins import PluginManifest


class PackagePin(Contract):
    entry_point: str
    version: str = Field(min_length=1)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    contract_version: Literal["1"] = "1"


EXTENSIONS: dict[str, dict[str, Any]] = {}
PINS: list[PackagePin] = []
_owners: dict[tuple[str, str], PackagePin] = {}
_plugins: dict[str, dict[str, Any]] = {}
_pin_plugins: dict[str, str] = {}
_loader: Callable[..., Any] | None = None


@dataclass(frozen=True)
class _Generation:
    extensions: dict[str, dict[str, Any]]
    pins: tuple[PackagePin, ...]
    owners: dict[tuple[str, str], PackagePin]
    plugins: dict[str, dict[str, Any]]
    pin_plugins: dict[str, str]


_generation = _Generation({}, (), {}, {}, {})
_operation: ContextVar[_Generation | None] = ContextVar("plugin_generation", default=None)


def _view() -> _Generation:
    return _operation.get() or _generation


@contextmanager
def registry_scope() -> Generator[None]:
    """Bind authorization and execution to one published release set, also across awaits."""
    token = _operation.set(_view())
    try:
        yield
    finally:
        _operation.reset(token)


def registry_operation[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def asynchronous(*args: P.args, **kwargs: P.kwargs) -> Any:
            with registry_scope():
                return await fn(*args, **kwargs)

        return cast(Callable[P, R], asynchronous)

    @functools.wraps(fn)
    def synchronous(*args: P.args, **kwargs: P.kwargs) -> R:
        with registry_scope():
            return fn(*args, **kwargs)

    return synchronous


def installed_pins() -> tuple[PackagePin, ...]:
    return _view().pins


def configure_extension_loader(loader: Callable[..., Any]) -> None:
    global _loader
    _loader = loader


def load_extensions(entry_points: list[str], expected: list[PackagePin] | None = None) -> None:
    """Re-describe releases before atomic registration; equal names do not prove equal assets."""
    global _generation
    if entry_points and _loader is None:
        raise RuntimeError("configure an isolated plugin loader before loading plugins")
    pending: dict[str, dict[str, Any]] = {}
    pins: list[PackagePin] = []
    owners: dict[tuple[str, str], PackagePin] = {}
    plugins: dict[str, dict[str, Any]] = {}
    pin_plugins: dict[str, str] = {}
    for reference in entry_points:
        assert _loader is not None
        metadata, sections = _loader(reference)
        pin = PackagePin.model_validate(
            {k: metadata[k] for k in ("entry_point", "version", "digest", "contract_version")}
        )
        if any(p.entry_point == pin.entry_point for p in pins):
            raise ValueError(f"duplicate plugin release {pin.entry_point}")
        raw = sections.get("plugin", {}).get("manifest") or metadata.get("manifest")
        if not raw:
            # Explicit compatibility registration, never a production default.
            raw = {
                "id": pin.entry_point.split(":")[0].replace("_", "-"),
                "version": pin.version,
                "title": pin.entry_point.split(":")[0],
                "capabilities": list(sections.get("cases", {})),
                "roles": [{"id": r, "label": r} for r in sections.get("roles", {})],
                "tools": sorted(
                    {t for ts in sections.get("workflow_required_tools", {}).values() for t in ts}
                    | {t for ts in sections.get("workflow_dependencies", {}).values() for t in ts}
                ),
                "skills": [{"id": s} for s in sections.get("skill_documents", {})],
            }
        manifest = PluginManifest.model_validate(raw)
        if manifest.version != pin.version:
            raise ValueError("manifest version does not match release version")
        if manifest.id in plugins:
            raise ValueError(f"duplicate plugin ID {manifest.id}")
        if set(manifest.capabilities) != set(sections.get("cases", {})):
            raise ValueError(f"plugin {manifest.id} capability declaration mismatch")
        declared_knowledge = {item.id for item in manifest.knowledge}
        for knowledge_id in manifest.prompt_knowledge:
            document = sections.get("knowledge_documents", {}).get(knowledge_id)
            if knowledge_id not in declared_knowledge or not isinstance(document, dict):
                raise ValueError("prompt knowledge must be declared and owned by its plugin")
            if not isinstance(cast(dict[str, Any], document).get("content"), str):
                raise ValueError("prompt knowledge must have a text representation")
        plugins[manifest.id] = {**manifest.wire(), "packagePins": [pin.wire()]}
        pin_plugins[pin.entry_point] = manifest.id
        pins.append(pin)
        for kind, values in sections.items():
            if kind == "plugin":
                continue
            if not isinstance(kind, str) or not isinstance(values, dict):
                raise ValueError("extension sections must be named mappings")
            target = pending.setdefault(kind, {})
            for key, value in cast(dict[str, Any], values).items():
                validate_identifier(key)
                if key in target:
                    raise ValueError(f"duplicate extension {kind}:{key}")
                if kind == "presentation":
                    _validate_presentation(value)
                owners[kind, key] = pin
                if kind == "cases":
                    definition = CaseDef.model_validate(
                        value.wire() if isinstance(value, CaseDef) else value
                    )
                    if definition.id != key:
                        raise ValueError("case identifier must match its registry key")
                    target[key] = definition
                else:
                    target[key] = value
    for plugin in plugins.values():
        for dep in plugin["dependencies"]:
            if dep not in plugins:
                raise ValueError(f"plugin {plugin['id']} requires unavailable plugin {dep}")
    if expected is not None and expected != pins:
        raise ValueError("installed package pins do not match deployment lock")
    # Publication is one pointer assignment. Legacy inspection aliases below are not used
    # for production admission; in-flight operations retain their captured generation.
    _generation = _Generation(pending, tuple(pins), owners, plugins, pin_plugins)
    EXTENSIONS.clear()
    EXTENSIONS.update(pending)
    CASES.clear()
    CASES.update(pending.get("cases", {}))
    PINS[:] = pins
    _owners.clear()
    _owners.update(owners)
    _plugins.clear()
    _plugins.update(plugins)
    _pin_plugins.clear()
    _pin_plugins.update(pin_plugins)


def clear_extensions() -> None:
    global _generation
    _generation = _Generation({}, (), {}, {}, {})
    EXTENSIONS.clear()
    CASES.clear()
    PINS.clear()
    _owners.clear()
    _plugins.clear()
    _pin_plugins.clear()


def extension(kind: str, name: str) -> Any:
    try:
        return _view().extensions[kind][name]
    except KeyError as exc:
        raise LookupError(f"extension not installed: {kind}:{name}") from exc


def optional_extension(kind: str, name: str) -> Any:
    return _view().extensions.get(kind, {}).get(name)


def all_extensions(kind: str) -> dict[str, Any]:
    return dict(_view().extensions.get(kind, {}))


def default_case() -> str:
    return next(iter(_view().extensions.get("cases", {})), "")


def plugin_descriptors() -> list[dict[str, Any]]:
    return deepcopy(list(_view().plugins.values()))


def plugins_for(ids: list[str]) -> list[dict[str, Any]]:
    plugins = _view().plugins
    selected: dict[str, dict[str, Any]] = {}
    visiting: set[str] = set()

    def visit(pid: str) -> None:
        if pid in visiting:
            raise ValueError("cyclic plugin dependency")
        if pid in selected:
            return
        if pid not in plugins:
            raise LookupError(f"plugin not installed: {pid}")
        visiting.add(pid)
        for dep in plugins[pid]["dependencies"]:
            visit(dep)
        visiting.remove(pid)
        selected[pid] = plugins[pid]

    for pid in ids:
        visit(pid)
    return deepcopy(list(selected.values()))


@registry_operation
def package_pins(plugin_ids: list[str] | None = None) -> list[dict[str, Any]]:
    selected = None if plugin_ids is None else {p["id"] for p in plugins_for(plugin_ids)}
    view = _view()
    return [
        p.wire()
        for p in view.pins
        if selected is None or view.pin_plugins[p.entry_point] in selected
    ]


def package_for(kind: str, name: str) -> PackagePin | None:
    return _view().owners.get((kind, name))


def plugin_for(kind: str, name: str) -> dict[str, Any] | None:
    view = _view()
    pin = view.owners.get((kind, name))
    return None if pin is None else deepcopy(view.plugins[view.pin_plugins[pin.entry_point]])


def _validate_presentation(value: Any) -> None:
    """Do not advertise UI operations or schemas the versioned host cannot execute."""
    if not isinstance(value, dict):
        raise ValueError("presentation must be descriptor data")
    value = cast(dict[str, Any], value)
    actions = [{"id": "solve", "label": "Run", "kind": "run"}]
    if "actions" in value and value["actions"] != actions:
        raise ValueError("contract v1 presentation supports only the canonical Run action")
    if "inputSchema" in value:
        raise ValueError("contract v1 input schema must be declared through case lanes")
    if value.get("outputs"):
        raise ValueError("contract v1 outputs are supplied through result records")


@registry_operation
def describe_case(case: CaseDef) -> dict[str, Any]:
    out = case.model_dump(mode="json", by_alias=False)
    out["description"] = case.grammar
    for lane in out["lanes"]:
        for field in lane["fields"]:
            field.update(id=field["name"], type=field["kind"], options=field["choices"])
    out["actions"] = [{"id": "solve", "label": "Run", "kind": "run"}]
    out["outputs"] = []
    plugin = plugin_for("cases", case.id)
    out["pluginId"] = plugin["id"] if plugin else ""
    out["roles"] = plugin["roles"] if plugin else []
    out["packagePins"] = plugin["packagePins"] if plugin else []
    presentation = optional_extension("presentation", case.id)
    if isinstance(presentation, dict):
        out.update(
            {
                k: v
                for k, v in cast(dict[str, Any], presentation).items()
                if k
                in (
                    "actions",
                    "outputs",
                    "icon",
                    "color",
                    "subtitle",
                    "intakeBindings",
                )
            }
        )
    return out
