"""A2UI v0.9 for Consilith: one closed catalog and a validator (FR-PR-3/4, FR-UI-1..5).

Messages are built as plain JSON (``"version": "v0.9"``). Before any message is stored or
sent it passes ``validate``:
- the catalog id is Consilith's; ``createSurface`` comes once, before updates;
- components are known catalog components with valid props; exactly one ``root``; no
  orphans and no dangling child ids;
- every number in a data model sits inside an object whose ``provenanceId`` resolves to a
  stored result, ledger entry or spec value (FR-UI-1); ``/meta`` is exempt (phases, wiring);
- unknown values are ``{"placeholder": "..."}`` (FR-UI-4); assumed ones carry
  ``"illustrative": true`` (FR-UI-5);
- a surface holds at most ``MAX_COMPONENTS`` components and nests at most ``MAX_DEPTH`` deep,
  the same caps the browser applies (``apps/web/src/shared/a2ui/surface.ts``).

This module is the single source of the catalog's names. ``tools/gen_catalog.py`` writes the
TypeScript lists from ``catalog_manifest()``, and a test fails when the two differ.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

VERSION = "v0.9"
CATALOG_ID = "https://consilith.local/a2ui/catalogs/consilith/v0_9"
MEDIA_TYPE = "application/a2ui+json"
PHASES = ("absent", "skeleton", "materialising", "live", "rest")
# The caps a surface keeps on the server and in the browser.
MAX_COMPONENTS = 500
MAX_DEPTH = 24
# Closed value sets. The ``Literal`` types below repeat them (pyright needs literals); a test
# keeps each pair equal.
TEXT_VARIANTS = ("body", "caption", "h1", "h2", "h3")
LAYOUT_KINDS = ("stack", "grid", "split", "tabs", "section")
LAYOUT_RATIOS = ("balanced", "focus-start", "focus-end")
LAYOUT_EMPHASES = ("plain", "surface", "hero", "quiet")
LAYOUT_DENSITIES = ("comfortable", "compact")
# Provenance ids of the server's illustrative set (protocols/illustrative.py). A value that cites
# one must say ``"illustrative": true``: it is design data, not a result.
ILLUSTRATIVE_PREFIX = "ill-v1:"


class A2UIError(ValueError):
    """``VALIDATION_FAILED``: ``path`` points into the offending message."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(pattern=r"^/")


TextValue = str | Binding


class Props(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    component: str
    accessibility: dict[str, str] | None = None
    weight: float | None = None


class Container(Props):
    children: list[str] = Field(default_factory=list[str])


class WorkspaceLayoutProps(Container):
    kind: Literal["stack", "grid", "split", "tabs", "section"]
    title: str = ""
    description: str = ""
    ratio: Literal["balanced", "focus-start", "focus-end"] = "balanced"
    emphasis: Literal["plain", "surface", "hero", "quiet"] = "plain"
    density: Literal["comfortable", "compact"] = "comfortable"
    labels: list[str] = Field(default_factory=list[str])


class CardProps(Props):
    child: str


class TextProps(Props):
    text: TextValue
    variant: Literal["body", "caption", "h1", "h2", "h3"] | None = None


class ValueRowProps(Props):
    label: str
    value: Binding  # a Quantity, a placeholder or text
    note: TextValue | None = None


class PanelProps(Props):
    """Every BRD panel: a title, its stamp, its provenance line and its rows."""

    title: TextValue
    stamp: Binding | None = None
    provenance: Binding | None = None
    phase: Binding | None = None
    children: list[str] = Field(default_factory=list[str])


class DataProps(Props):
    """Fields, plots and lists drawn by the renderer from one bound value."""

    title: TextValue | None = None
    data: Binding
    variant: str | None = None


class ApprovalProps(Props):
    """Shown only to the lane owner; the reason is required on both actions (FR-MP-5)."""

    proposal: Binding
    action: dict[str, Any] = Field(default_factory=lambda: {"event": {"name": "decide"}})


class ChipsProps(Props):
    chips: Binding


PANELS = (
    "Panel",
    "SeatPanel",
    "AgentCard",
    "RouteCard",
    "RoutePlan",
    "ChecklistPanel",
    "ChecksPanel",
    "CandidatePanel",
    "FailurePanel",
    "TensionCard",
)
DATA_VIEWS = (
    "PresencePill",
    "DecisionLog",
    "GhostCompletion",
    "HypothesisBars",
    "TradeOffFront",
    "WarningChip",
    "GhostProposal",
    "EventStream",
    "PermissionsMatrix",
    "MCPCallTable",
    "LedgerEntry",
    "MemoryList",
    "SchemaForm",
    "ParameterGrid",
    "ArtifactView",
    "ResultTable",
    "MetricGrid",
    "JobStatus",
    "EvidenceView",
    "Chart",
    "Heatmap",
)
# The variants a data view honours. A view listed here refuses any other variant; a view not
# listed takes none of its own and ignores the field. The first entry is the default.
VARIANTS: dict[str, tuple[str, ...]] = {
    "ResultTable": ("table", "compact"),
    "MetricGrid": ("tiles", "strip"),
    "Chart": ("line", "scatter", "bar"),
    "Heatmap": ("sequential", "diverging"),
    "GhostProposal": ("waiting-for-owner", "read-only"),
}
CATALOG: dict[str, type[Props]] = {
    "WorkspaceLayout": WorkspaceLayoutProps,
    "Column": Container,
    "Row": Container,
    "Card": CardProps,
    "Text": TextProps,
    "Divider": Props,
    "ValueRow": ValueRowProps,
    "ApprovalChip": ApprovalProps,
    "NextCalcChips": ChipsProps,
    **{name: PanelProps for name in PANELS},
    **{name: DataProps for name in DATA_VIEWS},
}

Resolver = Callable[[str], bool]


def create_surface(surface_id: str) -> dict[str, Any]:
    return {"version": VERSION, "createSurface": {"surfaceId": surface_id, "catalogId": CATALOG_ID}}


def update_components(surface_id: str, components: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": VERSION,
        "updateComponents": {"surfaceId": surface_id, "components": components},
    }


def update_data(surface_id: str, value: Any, path: str = "/") -> dict[str, Any]:
    return {
        "version": VERSION,
        "updateDataModel": {"surfaceId": surface_id, "path": path, "value": value},
    }


def catalog_manifest() -> dict[str, Any]:
    """Everything the browser must mirror: component names and the closed value sets."""
    return {
        "catalogId": CATALOG_ID,
        "version": VERSION,
        "panels": list(PANELS),
        "dataViews": list(DATA_VIEWS),
        "textVariants": list(TEXT_VARIANTS),
        "layoutKinds": list(LAYOUT_KINDS),
        "layoutRatios": list(LAYOUT_RATIOS),
        "layoutEmphases": list(LAYOUT_EMPHASES),
        "layoutDensities": list(LAYOUT_DENSITIES),
        "variants": {name: list(values) for name, values in VARIANTS.items()},
        "maxComponents": MAX_COMPONENTS,
        "maxDepth": MAX_DEPTH,
    }


def _children(comp: Props) -> list[str]:
    if isinstance(comp, Container | PanelProps):
        return list(comp.children)
    if isinstance(comp, CardProps):
        return [comp.child]
    return []


def validate_components(components: list[dict[str, Any]], at: str) -> None:
    parsed: dict[str, Props] = {}
    for i, raw in enumerate(components):
        name = raw.get("component")
        model = CATALOG.get(str(name))
        if model is None:
            raise A2UIError(
                f"{at}/components/{i}/component", f"{name!r} is not in the Consilith catalog"
            )
        try:
            comp = model.model_validate(raw)
        except ValidationError as exc:
            first = exc.errors()[0]
            loc = "/".join(str(x) for x in first["loc"])
            raise A2UIError(f"{at}/components/{i}/{loc}", first["msg"]) from exc
        if comp.id in parsed:
            raise A2UIError(f"{at}/components/{i}/id", f"duplicate component id {comp.id!r}")
        allowed = VARIANTS.get(comp.component)
        variant = comp.variant if isinstance(comp, DataProps) else None
        if allowed is not None and variant is not None and variant not in allowed:
            raise A2UIError(
                f"{at}/components/{i}/variant",
                f"{comp.component} takes the variants {', '.join(allowed)}, not {variant!r}",
            )
        parsed[comp.id] = comp
    if len(parsed) > MAX_COMPONENTS:
        raise A2UIError(f"{at}/components", f"a surface holds at most {MAX_COMPONENTS} components")
    if "root" not in parsed:
        raise A2UIError(f"{at}/components", "exactly one component must have id 'root'")
    seen: set[str] = set()
    stack: list[tuple[str, int]] = [("root", 0)]
    while stack:
        cid, depth = stack.pop()
        if cid in seen:
            raise A2UIError(f"{at}/components", f"component {cid!r} is reachable twice (a cycle)")
        if depth > MAX_DEPTH:
            raise A2UIError(f"{at}/components", f"a surface nests at most {MAX_DEPTH} deep")
        seen.add(cid)
        for child in _children(parsed[cid]):
            if child not in parsed:
                raise A2UIError(f"{at}/components", f"{cid!r} names a missing child {child!r}")
            stack.append((child, depth + 1))
    orphans = sorted(set(parsed) - seen)
    if orphans:
        raise A2UIError(f"{at}/components", f"orphan components not reachable from root: {orphans}")


def validate_data(value: Any, at: str, resolve: Resolver, *, path: str = "/") -> None:
    """Every number needs a resolving ``provenanceId`` on itself or an enclosing object."""

    def walk(node: Any, where: str, prov: str | None, *, opaque: bool = False) -> None:
        if isinstance(node, bool) or node is None or isinstance(node, str):
            return
        if isinstance(node, int | float):
            if prov is None:
                raise A2UIError(where, "a number without a provenanceId is refused (FR-UI-1)")
            return
        if isinstance(node, list):
            for i, item in enumerate(cast(list[Any], node)):
                walk(item, f"{where}/{i}", prov, opaque=opaque)
            return
        if isinstance(node, dict):
            obj = cast(dict[str, Any], node)
            pid = obj.get("provenanceId") if not opaque else None
            if pid is not None:
                if not isinstance(pid, str) or not resolve(pid):
                    raise A2UIError(f"{where}/provenanceId", f"provenance {pid!r} does not resolve")
                if pid.startswith(ILLUSTRATIVE_PREFIX) and obj.get("illustrative") is not True:
                    raise A2UIError(
                        f"{where}/illustrative",
                        "an illustrative value is marked illustrative: true",
                    )
                prov = pid
            if not opaque and "placeholder" in obj and not isinstance(obj["placeholder"], str):
                raise A2UIError(f"{where}/placeholder", "a placeholder is bracketed text")
            for key, item in obj.items():
                if where == "" and key == "meta":
                    continue  # /meta: phases, wiring and timing, not displayed science
                walk(
                    item,
                    f"{where}/{key}",
                    prov,
                    opaque=opaque or (obj.get("kind") == "json" and key == "value"),
                )

    base = "" if path == "/" else path.rstrip("/")
    if path.startswith("/meta"):
        return
    walk(value, base, None)
    _ = at


def validate(
    messages: list[dict[str, Any]], resolve: Resolver, created: set[str] | None = None
) -> set[str]:
    """Validate a batch for one surface stream. Returns the surface ids now created."""
    created = set(created or ())
    for i, msg in enumerate(messages):
        at = f"/{i}"
        if msg.get("version") != VERSION:
            raise A2UIError(f"{at}/version", f"expected {VERSION}")
        kinds = [
            k
            for k in ("createSurface", "updateComponents", "updateDataModel", "deleteSurface")
            if k in msg
        ]
        if len(kinds) != 1 or len(msg) != 2:
            raise A2UIError(at, "a message carries version and exactly one operation")
        body = cast(dict[str, Any], msg[kinds[0]])
        sid = body.get("surfaceId")
        if not isinstance(sid, str) or not sid:
            raise A2UIError(f"{at}/{kinds[0]}/surfaceId", "required")
        if kinds[0] == "createSurface":
            if sid in created:
                raise A2UIError(f"{at}/createSurface", f"surface {sid!r} already exists")
            if body.get("catalogId") != CATALOG_ID:
                raise A2UIError(
                    f"{at}/createSurface/catalogId", "only the Consilith catalog renders"
                )
            created.add(sid)
            continue
        if kinds[0] == "deleteSurface":
            created.discard(sid)
            continue
        if sid not in created:
            raise A2UIError(f"{at}/{kinds[0]}", f"surface {sid!r} was not created first")
        if kinds[0] == "updateComponents":
            validate_components(
                cast(list[dict[str, Any]], body.get("components") or []), f"{at}/updateComponents"
            )
        else:
            validate_data(
                body.get("value"), f"{at}/updateDataModel", resolve, path=str(body.get("path", "/"))
            )
    return created


def quantity(value: float, unit: str, provenance_id: str, **extra: Any) -> dict[str, Any]:
    return {"v": value, "unit": unit, "provenanceId": provenance_id, **extra}


def placeholder(label: str) -> dict[str, str]:
    return {"placeholder": label}
