"""Generative UI from chat: the binder and the ``ui.*`` and ``studio.compose`` ops (section 8).

The model writes a refs-only ``AdHocPlan``. The server issues a per-turn index of handles
(``r1``, ``in3``, ``a2``, ``run1``, ``p1``) for results, input fields, artifacts, runs and
proposals, and this module binds the plan to the data behind those handles. No model ever writes
a number into a surface: a number reaches a surface only through a stored result or a twin value,
and it carries that record's provenance id. ``a2ui.validate`` is the last gate, so a surface
with an unreferenced number cannot be built.

Rules the binder keeps:

* an unknown handle, or a handle of the wrong kind for its block, is refused with the list of
  handles the model may use;
* a series from a result that is not current is refused unless the person asked for history;
  one that is current but not verified is drawn with its stamp and a banner;
* at most 500 components, 24 deep, 2000 points by 8 series per chart and a 128 by 128 heatmap,
  all cut by selection only (never averaging or interpolation);
* titles pass the prose guard, and a titled block says who wrote it;
* ad-hoc layouts use ``stack``, ``grid``, ``tabs`` and ``section`` only (no ``hero`` or ``split``),
  carry no approval or run control, and are private to the viewer (``ad:{viewer}:{id}``).

A deterministic plan built from the handles alone is the fallback when the model's plan cannot
be bound, and the path the scripted tests use.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from consilith_contracts import (
    AdHocBlock,
    AdHocComponent,
    AdHocLayoutNode,
    AdHocPlan,
    Autonomy,
    RiskClass,
    new_id,
)
from consilith_contracts.results import ResultRecord, ResultStatus
from consilith_contracts.room import Proposal
from pydantic import ValidationError

from ..domain.errors import NotFound, RoomError
from ..domain.service import RoomService
from ..protocols import a2ui
from ..protocols.journal import Journal
from .composer import (
    _provenance,  # pyright: ignore[reportPrivateUsage]
    _q,  # pyright: ignore[reportPrivateUsage]
    _spec,  # pyright: ignore[reportPrivateUsage]
    resolver,
)
from .platform_tools import OpContext, OpResult, op
from .prose_guard import GuardContext, check_prose
from .turn_events import Surface

HandleKind = Literal["result", "input", "artifact", "run", "proposal"]
ChartKind = Literal["line", "scatter", "bar"]

# --- caps ------------------------------------------------------------------------------------

MAX_BLOCK_REFS = 40  # handles in one block
CHART_POINTS = 2000  # points in one series
CHART_SERIES = 8
HEATMAP_SIDE = 128  # cells along either axis
MAX_HANDLES = 120  # what one turn's index holds
MAX_SURFACES_PER_TURN = 3
ARTIFACT_READ_BYTES = 4 * 1024 * 1024

_PREFIX: dict[HandleKind, str] = {
    "result": "r",
    "input": "in",
    "artifact": "a",
    "run": "run",
    "proposal": "p",
}
_STAMP_RANK = {"blocked": 0, "warning": 1, "checking": 2, "verified": 3}
# Blocks that put a number on screen. A result that is not current is refused there.
_NUMERIC_BLOCKS = frozenset(
    {
        AdHocComponent.VALUES,
        AdHocComponent.METRICS,
        AdHocComponent.TABLE,
        AdHocComponent.CHART,
        AdHocComponent.HEATMAP,
    }
)
# Which handle kinds each component takes.
_KINDS: dict[AdHocComponent, frozenset[HandleKind]] = {
    AdHocComponent.VALUES: frozenset({"result", "input"}),
    AdHocComponent.METRICS: frozenset({"result"}),
    AdHocComponent.TABLE: frozenset({"result"}),
    AdHocComponent.CHART: frozenset({"result"}),
    AdHocComponent.HEATMAP: frozenset({"result"}),
    AdHocComponent.INPUTS: frozenset({"input"}),
    AdHocComponent.ARTIFACT: frozenset({"artifact", "result"}),
    AdHocComponent.EVIDENCE: frozenset({"result", "proposal"}),
    AdHocComponent.JOB: frozenset({"run"}),
    AdHocComponent.NOTE: frozenset(),
}
# A bind that fails for one of these reasons is a defect of the surface, not of the request, so
# a retry or the deterministic fallback may fix it. The others are refusals and stay refusals.
_REPAIRABLE = frozenset({"invalid_surface", "too_large", "title_has_number"})


# --- the handle index ------------------------------------------------------------------------


@dataclass(frozen=True)
class Handle:
    """One thing the model may point at. Server-built; the model sees the name and the label."""

    name: str
    kind: HandleKind
    id: str  # a result id, ``case/lane/field``, an artifact sha256, a run id or a proposal id
    label: str
    unit: str = ""
    stamp: str = ""
    status: str = ""
    case: str = ""
    lane: str = ""
    output: str = ""  # a result's output kind: number, text, category, json or artifact

    @property
    def current(self) -> bool:
        return self.status in ("", ResultStatus.CURRENT.value)

    def line(self) -> str:
        """The handle as the prompt shows it: no value, only what the model needs to choose."""
        bits = [f"{self.name} {self.kind}", f'"{self.label}"']
        if self.output and self.output != "number":
            bits.append(f"kind {self.output}")
        if self.unit:
            bits.append(f"unit {self.unit}")
        if self.stamp:
            bits.append(f"stamp {self.stamp}")
        if self.status and self.status != ResultStatus.CURRENT.value:
            bits.append(f"status {self.status}")
        return " ".join(bits)


HandleIndex = Mapping[str, Handle]


def _results(room: RoomService, case: str | None) -> list[ResultRecord]:
    out: list[ResultRecord] = []
    for node in cast(dict[str, Any], room.read("results") or {}).values():
        rec = ResultRecord.model_validate(node)
        if case is None or rec.case == case:
            out.append(rec)
    out.sort(key=lambda r: (r.status != ResultStatus.CURRENT, r.case, r.quantity, r.id))
    return out


def issue_handles(room: RoomService, viewer: str, *, case: str | None = None) -> dict[str, Handle]:
    """The handle index for one turn. Deterministic: the same room state gives the same names,
    so the prompt and the binder agree. Results come current first, then older ones."""
    _ = viewer
    out: dict[str, Handle] = {}
    counts: dict[HandleKind, int] = dict.fromkeys(_PREFIX, 0)

    def add(kind: HandleKind, **fields: Any) -> None:
        if len(out) >= MAX_HANDLES:
            return
        counts[kind] += 1
        name = f"{_PREFIX[kind]}{counts[kind]}"
        out[name] = Handle(name=name, kind=kind, **fields)

    records = _results(room, case)
    for rec in records:
        add(
            "result",
            id=rec.id,
            label=rec.quantity,
            unit=rec.unit if rec.kind == "number" else "",
            stamp=rec.stamp.value,
            status=rec.status.value,
            case=rec.case,
            output=rec.kind,
        )
    for cid, cdef in sorted(room.case_definitions().items()):
        if case is not None and cid != case:
            continue
        for lane in cdef.lanes:
            for fdef in lane.fields:
                add(
                    "input",
                    id=f"{cid}/{lane.id}/{fdef.name}",
                    label=f"{lane.label} / {fdef.label or fdef.name}",
                    unit=fdef.unit or "",
                    case=cid,
                    lane=lane.id,
                )
    seen: set[str] = set()
    for rec in records:
        for ref in rec.artifacts:
            if ref.sha256 not in seen:
                seen.add(ref.sha256)
                add(
                    "artifact",
                    id=ref.sha256,
                    label=f"{rec.quantity} ({ref.kind})",
                    status=rec.status.value,
                    case=rec.case,
                )
    runs = cast(dict[str, dict[str, Any]], room.read("runs") or {})
    for rid, run in sorted(runs.items()):
        if case is None or run.get("case") == case:
            add(
                "run",
                id=rid,
                label=str(run.get("tool", "run")),
                status=str(run.get("status", "")),
                case=str(run.get("case", "")),
            )
    for node in cast(dict[str, Any], room.read("proposals") or {}).values():
        prop = Proposal.model_validate(node)
        if case is None or prop.case == case:
            add(
                "proposal",
                id=prop.id,
                label=f"proposal in {prop.case}/{prop.target_lane}",
                status=prop.status.value,
                case=prop.case,
                lane=prop.target_lane,
            )
    return out


def render_handles(handles: HandleIndex) -> str:
    """The index as prompt text, one handle a line."""
    if not handles:
        return "HANDLES: none yet (nothing has been computed and no inputs are set)"
    return "HANDLES (use only these names in refs)\n" + "\n".join(
        h.line() for h in handles.values()
    )


# --- errors and options ----------------------------------------------------------------------


class BindError(RoomError):
    """A plan could not be bound. ``feedback`` is plain words the model can act on."""

    status = 422
    code = "bind_failed"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message, code=code)
        self.feedback = message


@dataclass(frozen=True)
class BlockOptions:
    """What a block needs that the refs-only plan cannot say: column names and the chart kind."""

    x: str | None = None
    y: tuple[str, ...] = ()
    kind: ChartKind = "line"


@dataclass(frozen=True)
class Origin:
    """Who wrote the plan, for attribution and ``meta.origin``."""

    agent: str = ""
    model: str = ""
    chat: str = "chat"
    message_id: str = ""

    def by(self) -> str:
        who = self.agent or "your agent"
        return f"written by {who} ({self.model})" if self.model else f"written by {who}"


# --- tables out of stored results ------------------------------------------------------------

Cell = float | str
Columns = dict[str, list[Cell]]


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _cell(value: object) -> Cell:
    number = _finite(value)
    return number if number is not None else ("" if value is None else str(value))


def _as_columns(value: object) -> tuple[Columns, dict[str, str]]:
    """A table from a stored JSON value, with the units it declares. Shapes taken: a dict of
    equal-length lists, ``{"columns": [...], "rows": [[...]]}`` and a list of dicts."""
    units: dict[str, str] = {}
    if isinstance(value, dict):
        node = cast(dict[str, Any], value)
        declared = node.get("units")
        if isinstance(declared, dict):
            units = {str(k): str(v) for k, v in cast(dict[str, Any], declared).items()}
        names, rows = node.get("columns"), node.get("rows")
        if isinstance(names, list) and isinstance(rows, list):
            keys = [str(n) for n in cast(list[Any], names)]
            cols: Columns = {k: [] for k in keys}
            for row in cast(list[Any], rows):
                if isinstance(row, list) and len(cast(list[Any], row)) == len(keys):
                    for key, cell in zip(keys, cast(list[Any], row), strict=True):
                        cols[key].append(_cell(cell))
            return cols, units
        lists = {
            str(k): cast(list[Any], v)
            for k, v in node.items()
            if isinstance(v, list) and k not in ("columns", "rows")
        }
        if lists and len({len(v) for v in lists.values()}) == 1:
            return {k: [_cell(c) for c in v] for k, v in lists.items()}, units
    if isinstance(value, list):
        items = cast(list[Any], value)
        if items and all(isinstance(r, dict) for r in items):
            records = cast(list[dict[str, Any]], items)
            return {k: [_cell(r.get(k)) for r in records] for k in records[0]}, units
    return {}, units


def _csv_cell(text: str) -> object:
    try:
        return float(text)
    except ValueError:
        return text


def _artifact_values(room: RoomService, rec: ResultRecord) -> list[object]:
    """The parsed JSON or CSV of a result's stored artifacts, one entry per readable artifact."""
    out: list[object] = []
    for ref in rec.artifacts:
        stored = room.store.artifact(ref.sha256)
        if stored is None:
            continue
        media, data = stored
        if len(data) > ARTIFACT_READ_BYTES:
            continue
        kind = f"{media} {ref.media_type}"
        try:
            text = data.decode("utf-8")
            if "json" in kind:
                out.append(json.loads(text))
            elif "csv" in kind:
                rows = list(csv.reader(io.StringIO(text)))
                if len(rows) > 1:
                    out.append(
                        {"columns": rows[0], "rows": [[_csv_cell(c) for c in r] for r in rows[1:]]}
                    )
        except (UnicodeDecodeError, ValueError, csv.Error):
            continue
    return out


def result_table(room: RoomService, rec: ResultRecord) -> tuple[Columns, dict[str, str]]:
    """The columns a chart may pick from, for one stored result (empty when it has none)."""
    sources: list[object] = []
    if rec.kind == "json":
        sources.append(rec.value)
    elif rec.kind == "artifact":
        sources.extend(_artifact_values(room, rec))
    for source in sources:
        cols, units = _as_columns(source)
        if cols:
            return cols, units
    return {}, {}


def _select(n: int, cap: int) -> list[int]:
    """At most ``cap`` indices out of ``n``, evenly spaced, first and last always kept. This is
    selection: no value is averaged or interpolated."""
    if n <= cap:
        return list(range(n))
    if cap <= 1:
        return [0]
    return sorted({round(i * (n - 1) / (cap - 1)) for i in range(cap)})


def _numeric(column: list[Cell]) -> bool:
    return bool(column) and sum(1 for c in column if _finite(c) is not None) * 2 >= len(column)


def _default_x(cols: Columns) -> str:
    for name in cols:
        if name.lower() in ("x", "t", "time", "step", "position", "distance", "r"):
            return name
    return next(iter(cols))


def _worst_stamp(series: Sequence[Mapping[str, Any]]) -> str:
    return min((str(s["stamp"]) for s in series), key=lambda s: _STAMP_RANK.get(s, 2))


def _worst_status(series: Sequence[Mapping[str, Any]]) -> str:
    odd = [str(s["status"]) for s in series if s["status"] != ResultStatus.CURRENT.value]
    return odd[0] if odd else ResultStatus.CURRENT.value


@dataclass(frozen=True)
class _Grid:
    rows: list[list[float]]
    extent: list[float] | None
    unit: str
    scale: str | None


def _grid_of(source: object) -> _Grid | None:
    """A rectangular grid of finite numbers out of a stored value, with its extent, unit and
    colour scale when it declares them."""
    node: object = source
    extent: list[float] | None = None
    unit = ""
    scale: str | None = None
    if isinstance(source, dict):
        d = cast(dict[str, Any], source)
        unit = str(d.get("unit", "") or "")
        raw_scale = d.get("colorScale")
        scale = raw_scale if isinstance(raw_scale, str) else None
        raw_extent = d.get("extent", d.get("bounds"))
        if isinstance(raw_extent, list) and len(cast(list[Any], raw_extent)) == 4:
            nums = [_finite(v) for v in cast(list[Any], raw_extent)]
            if all(n is not None for n in nums):
                extent = [cast(float, n) for n in nums]
        node = next((d[k] for k in ("values", "field", "grid", "z", "data") if k in d), None)
    if not isinstance(node, list) or not node:
        return None
    rows: list[list[float]] = []
    for raw in cast(list[Any], node):
        if not isinstance(raw, list) or not raw:
            return None
        line = [_finite(v) for v in cast(list[Any], raw)]
        if any(v is None for v in line) or (rows and len(line) != len(rows[0])):
            return None
        rows.append([cast(float, v) for v in line])
    return _Grid(rows, extent, unit, scale)


# --- the binder ------------------------------------------------------------------------------


class _Build:
    """The pieces of one surface while it is bound."""

    def __init__(
        self,
        room: RoomService,
        plan: AdHocPlan,
        handles: HandleIndex,
        options: Mapping[str, BlockOptions],
        origin: Origin,
        history: bool,
    ) -> None:
        self.room = room
        self.plan = plan
        self.handles = handles
        self.options = options
        self.origin = origin
        self.history = history
        self.comps: list[dict[str, Any]] = []
        self.blocks: dict[str, Any] = {}
        self.bound_results: dict[str, dict[str, str]] = {}
        self.bound_twin: dict[str, Any] = {}
        self.cases: list[str] = []
        self.layout_count = 0

    def add(self, comp: dict[str, Any]) -> str:
        self.comps.append(comp)
        return str(comp["id"])

    # handles ---------------------------------------------------------------------------
    def resolve(self, block: AdHocBlock) -> list[Handle]:
        if len(block.refs) > MAX_BLOCK_REFS:
            raise BindError(
                "too_large", f"block {block.id} names more than {MAX_BLOCK_REFS} handles"
            )
        allowed = _KINDS[block.component]
        out: list[Handle] = []
        for ref in block.refs:
            handle = self.handles.get(ref)
            if handle is None:
                raise BindError(
                    "unknown_handle",
                    f"block {block.id}: {ref!r} is not a handle. {render_handles(self.handles)}",
                )
            if handle.kind not in allowed:
                kinds = ", ".join(sorted(allowed)) or "none"
                raise BindError(
                    "wrong_kind",
                    f"block {block.id}: {ref} is a {handle.kind}, and "
                    f"{block.component.value} takes {kinds}",
                )
            if (
                block.component in _NUMERIC_BLOCKS
                and handle.kind == "result"
                and not handle.current
                and not self.history
            ):
                raise BindError(
                    "not_current",
                    f"block {block.id}: {ref} ({handle.label}) is {handle.status}, so it is not "
                    "shown as current. Show it only when the person asks for history.",
                )
            out.append(handle)
        if block.component is AdHocComponent.NOTE:
            if out:
                raise BindError("wrong_kind", f"block {block.id}: a note takes no handles")
        elif not out:
            raise BindError("no_refs", f"block {block.id} names no handles")
        return out

    def result(self, handle: Handle) -> ResultRecord:
        try:
            rec = self.room.result(handle.id)
        except NotFound as exc:
            raise BindError("unknown_handle", f"{handle.name} no longer exists") from exc
        self.bound_results[rec.id] = {"status": rec.status.value, "stamp": rec.stamp.value}
        self.note_case(rec.case)
        return rec

    def note_case(self, case: str) -> None:
        if case and case not in self.cases:
            self.cases.append(case)

    def bind_lane(self, case: str, lane: str) -> None:
        """Remember the lane's values as bound, so the browser can tell when they changed."""
        self.bound_twin[f"{case}/{lane}"] = self.room.read("twin", case, lane) or {}
        self.note_case(case)

    # blocks ----------------------------------------------------------------------------
    def block(self, block: AdHocBlock) -> str:
        handles = self.resolve(block)
        component = block.component
        if component is AdHocComponent.NOTE:
            return self.add(
                {"id": block.id, "component": "Text", "text": block.title, "variant": "body"}
            )
        kids: list[str] = []
        if component in (AdHocComponent.VALUES, AdHocComponent.INPUTS):
            rows = self.value_rows(handles)
            if block.title:
                kids.append(
                    self.add(
                        {
                            "id": f"{block.id}.title",
                            "component": "Text",
                            "text": block.title,
                            "variant": "h3",
                        }
                    )
                )
            for i, row in enumerate(rows):
                kids.append(
                    self.add(
                        {
                            "id": f"{block.id}.row{i}",
                            "component": "ValueRow",
                            "label": row["label"],
                            "value": {"path": f"/blocks/{block.id}/rows/{i}/value"},
                        }
                    )
                )
            self.blocks[block.id] = {"rows": rows}
        else:
            view, data = self.view(block, handles)
            kids.append(self.add(view))
            self.blocks[block.id] = data
        if block.title:
            kids.append(
                self.add(
                    {
                        "id": f"{block.id}.by",
                        "component": "Text",
                        "text": self.origin.by(),
                        "variant": "caption",
                    }
                )
            )
        return self.add({"id": block.id, "component": "Column", "children": kids})

    def value_rows(self, handles: Sequence[Handle]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for handle in handles:
            if handle.kind == "result":
                rec = self.result(handle)
                rows.append({"label": rec.quantity, "value": _q(rec, rec.quantity)})
                continue
            case, lane, name = handle.id.split("/", 2)
            self.bind_lane(case, lane)
            value = _spec(self.room, case, lane, name)
            node = self.room.read("twin", case, lane, name)
            if "text" in value and isinstance(node, bool):
                value = {"text": "yes" if node else "no"}
            elif "text" in value and isinstance(node, dict | list):
                value = {"text": "structured value: open it in the studio"}
            rows.append({"label": handle.label, "value": value})
        return rows

    def view(self, block: AdHocBlock, handles: Sequence[Handle]) -> tuple[dict[str, Any], Any]:
        component = block.component
        base: dict[str, Any] = {
            "id": f"{block.id}.view",
            "data": {"path": f"/blocks/{block.id}"},
        }
        if block.title:
            base["title"] = block.title
        if component in (AdHocComponent.METRICS, AdHocComponent.TABLE):
            items = [self.item(self.result(h)) for h in handles]
            name = "MetricGrid" if component is AdHocComponent.METRICS else "ResultTable"
            return {**base, "component": name}, {"title": block.title or "Results", "items": items}
        if component is AdHocComponent.CHART:
            kind = self.options.get(block.id, BlockOptions()).kind
            return {**base, "component": "Chart", "variant": kind}, self.chart(block, handles)
        if component is AdHocComponent.HEATMAP:
            heat = self.heatmap(block, handles)
            return {**base, "component": "Heatmap", "variant": heat["colorScale"]}, heat
        if component is AdHocComponent.ARTIFACT:
            return {**base, "component": "ArtifactView"}, self.artifacts(handles)
        if component is AdHocComponent.EVIDENCE:
            return self.evidence(block, base, handles)
        return {**base, "component": "JobStatus"}, self.job(block, handles)

    def item(self, rec: ResultRecord) -> dict[str, Any]:
        return {
            "name": rec.quantity,
            "quantity": _q(rec, rec.quantity),
            "provenance": _provenance(rec),
            "status": rec.status.value,
        }

    # chart -----------------------------------------------------------------------------
    def chart(self, block: AdHocBlock, handles: Sequence[Handle]) -> dict[str, Any]:
        opts = self.options.get(block.id, BlockOptions())
        records = [self.result(h) for h in handles]
        series: list[dict[str, Any]] = []
        x_label, y_label, x_unit, y_unit = opts.x or "", "", "", ""
        kind: ChartKind = opts.kind
        if not opts.x and all(r.kind == "number" for r in records):
            # Numbers side by side: one single-point series each, drawn as bars.
            kind = "bar"
            series = [self.number_series(r) for r in records]
            x_label = "result"
            units = {r.unit for r in records}
            y_label = records[0].unit if len(units) == 1 else "value"
            y_unit = records[0].unit if len(units) == 1 else ""
        else:
            for rec in records:
                cols, declared = result_table(self.room, rec)
                if not cols:
                    raise BindError(
                        "no_data",
                        f"{rec.quantity} has no table to chart. A chartable result is a json or "
                        "csv output with named columns.",
                    )
                xs = opts.x or _default_x(cols)
                ys = list(opts.y) or [c for c in cols if c != xs and _numeric(cols[c])]
                for name in [xs, *ys]:
                    if name not in cols:
                        raise BindError(
                            "no_column",
                            f"{rec.quantity} has no column {name!r}; columns: {', '.join(cols)}",
                        )
                if not ys:
                    raise BindError("no_column", f"{rec.quantity} has no numeric column to plot")
                x_label, x_unit = xs, declared.get(xs, x_unit)
                if len(ys) == 1 and not y_label:
                    y_label, y_unit = ys[0], declared.get(ys[0], "")
                elif not y_label or y_label != ys[0]:
                    y_label, y_unit = "value", ""
                series += [self.column_series(rec, cols, xs, name, kind) for name in ys]
        shown = series[:CHART_SERIES]
        primary = records[0]
        return {
            "provenanceId": primary.id,
            "title": block.title,
            "kind": kind,
            "xLabel": x_label,
            "yLabel": y_label,
            "xUnit": x_unit,
            "yUnit": y_unit,
            "stamp": _worst_stamp(shown),
            "status": _worst_status(shown),
            "tier": int(primary.tier),
            "ledgerId": primary.ledger_id,
            "omittedSeries": len(series) - len(shown),
            "series": shown,
        }

    def number_series(self, rec: ResultRecord) -> dict[str, Any]:
        value = _finite(rec.value)
        if value is None:
            raise BindError("no_data", f"{rec.quantity} is not a number")
        return {
            "name": rec.quantity,
            "provenanceId": rec.id,
            "stamp": rec.stamp.value,
            "status": rec.status.value,
            "tier": int(rec.tier),
            "ledgerId": rec.ledger_id,
            "unit": rec.unit,
            "x": [rec.quantity],
            "y": [value],
        }

    def column_series(
        self, rec: ResultRecord, cols: Columns, xs: str, ys: str, kind: ChartKind
    ) -> dict[str, Any]:
        pairs = [
            (x, y)
            for x, y in zip(cols[xs], cols[ys], strict=False)
            if _finite(y) is not None and (kind == "bar" or _finite(x) is not None)
        ]
        if not pairs:
            raise BindError("no_data", f"{rec.quantity}: column {ys!r} holds no numbers to plot")
        picked = [pairs[i] for i in _select(len(pairs), CHART_POINTS)]
        out: dict[str, Any] = {
            "name": ys,
            "provenanceId": rec.id,
            "stamp": rec.stamp.value,
            "status": rec.status.value,
            "tier": int(rec.tier),
            "ledgerId": rec.ledger_id,
            "x": [x for x, _ in picked],
            "y": [y for _, y in picked],
        }
        if len(pairs) > len(picked):
            out["decimatedFrom"] = len(pairs)
        return out

    # heatmap ---------------------------------------------------------------------------
    def heatmap(self, block: AdHocBlock, handles: Sequence[Handle]) -> dict[str, Any]:
        if len(handles) != 1:
            raise BindError("wrong_kind", f"block {block.id}: a heatmap shows one result")
        rec = self.result(handles[0])
        sources: list[object] = [rec.value] if rec.kind == "json" else []
        if rec.kind == "artifact":
            sources = _artifact_values(self.room, rec)
        found = next((g for g in map(_grid_of, sources) if g is not None), None)
        if found is None:
            raise BindError(
                "no_data",
                f"{rec.quantity} has no 2D scalar field to draw. A heatmap needs a rectangular "
                "grid of numbers under values, field, grid or z.",
            )
        ny, nx = len(found.rows), len(found.rows[0])
        flat = [v for row in found.rows for v in row]
        low, high = min(flat), max(flat)
        rows, cols = _select(ny, HEATMAP_SIDE), _select(nx, HEATMAP_SIDE)
        scale = found.scale if found.scale in a2ui.VARIANTS["Heatmap"] else None
        data: dict[str, Any] = {
            "provenanceId": rec.id,
            "title": block.title,
            "label": rec.quantity,
            "unit": found.unit or (rec.unit if rec.kind == "number" else ""),
            "stamp": rec.stamp.value,
            "status": rec.status.value,
            "tier": int(rec.tier),
            "ledgerId": rec.ledger_id,
            "grid": {"nx": len(cols), "ny": len(rows), "extent": found.extent},
            "values": [[found.rows[r][c] for c in cols] for r in rows],
            "min": low,
            "max": high,
            "colorScale": scale or ("diverging" if low < 0 < high else "sequential"),
        }
        if (len(cols), len(rows)) != (nx, ny):
            data["decimatedFrom"] = {"nx": nx, "ny": ny}
        return data

    # artifact, evidence, job -----------------------------------------------------------
    def artifacts(self, handles: Sequence[Handle]) -> dict[str, Any]:
        owners: dict[str, ResultRecord] = {}
        for rec in _results(self.room, None):
            for ref in rec.artifacts:
                owners.setdefault(ref.sha256, rec)
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for handle in handles:
            if handle.kind == "result":
                rec = self.result(handle)
                shas = [a.sha256 for a in rec.artifacts]
            else:
                shas = [handle.id]
            for sha in shas:
                owner = owners.get(sha)
                if owner is None:
                    raise BindError("unknown_handle", f"{handle.name} no longer exists")
                if sha in seen:
                    continue
                seen.add(sha)
                self.bound_results[owner.id] = {
                    "status": owner.status.value,
                    "stamp": owner.stamp.value,
                }
                self.note_case(owner.case)
                kind = next(a.kind for a in owner.artifacts if a.sha256 == sha)
                items.append(
                    {
                        "label": owner.quantity,
                        "sha256": sha,
                        "kind": kind,
                        "source": "computed",
                        "illustrative": owner.illustrative,
                        "provenanceId": owner.id,
                    }
                )
        return {"title": "Artifacts", "items": items}

    def evidence(
        self, block: AdHocBlock, base: dict[str, Any], handles: Sequence[Handle]
    ) -> tuple[dict[str, Any], Any]:
        items: list[dict[str, Any]] = []
        for handle in handles:
            if handle.kind == "result":
                rec = self.result(handle)
                items.append(
                    {
                        "name": rec.quantity,
                        "stamp": rec.stamp.value,
                        "status": rec.status.value,
                        "provenanceId": rec.id,
                        "ledgerId": rec.ledger_id,
                    }
                )
        stamps = {i["stamp"] for i in items}
        state = (
            "absent"
            if not items
            else "rejected"
            if "blocked" in stamps
            else "attention_required"
            if "warning" in stamps
            else "verified"
            if stamps == {"verified"}
            else "checking"
        )
        evidence = {"title": block.title or "Evidence", "state": state, "items": items}
        proposals = [h for h in handles if h.kind == "proposal"]
        if not proposals:
            return {**base, "component": "EvidenceView"}, evidence
        # Proposals are shown read-only. Approval is never offered from an ad-hoc surface.
        kids: list[str] = []
        data: dict[str, Any] = {"proposals": {}}
        if items:
            data["evidence"] = evidence
            kids.append(
                self.add(
                    {
                        "id": f"{block.id}.evidence",
                        "component": "EvidenceView",
                        "data": {"path": f"/blocks/{block.id}/evidence"},
                    }
                )
            )
        for handle in proposals:
            try:
                prop = self.room.proposal(handle.id)
            except NotFound as exc:
                raise BindError("unknown_handle", f"{handle.name} no longer exists") from exc
            self.note_case(prop.case)
            data["proposals"][prop.id] = {
                "id": prop.id,
                "provenanceId": prop.id,
                "author": prop.author,
                "lane": f"{prop.case}/{prop.target_lane}",
                "owner": prop.lane_owner,
                "reason": prop.reason,
                "status": prop.status.value,
            }
            kids.append(
                self.add(
                    {
                        "id": f"{block.id}.{handle.name}",
                        "component": "GhostProposal",
                        "data": {"path": f"/blocks/{block.id}/proposals/{prop.id}"},
                        "variant": "read-only",
                    }
                )
            )
        return {"id": f"{block.id}.view", "component": "Column", "children": kids}, data

    def job(self, block: AdHocBlock, handles: Sequence[Handle]) -> dict[str, Any]:
        runs: list[dict[str, Any]] = []
        for handle in handles:
            node = cast(dict[str, Any], self.room.read("runs", handle.id) or {})
            if not node:
                raise BindError("unknown_handle", f"{handle.name} no longer exists")
            self.note_case(str(node.get("case", "")))
            runs.append(
                {
                    "runId": handle.id,
                    "tool": str(node.get("tool", "")),
                    "status": str(node.get("status", "")),
                    "error": node.get("error"),
                }
            )
        last = runs[-1]
        return {
            "title": block.title or "Run",
            "case": self.cases[-1] if self.cases else "",
            "action": "none",
            "state": last["status"],
            "enabled": False,  # no run control: running happens from the studio or by a card
            "readOnly": True,
            "error": last["error"],
            "description": "",
            "runs": runs,
        }

    # layout ----------------------------------------------------------------------------
    def layout(self, node: AdHocLayoutNode, placed: Mapping[str, str]) -> str:
        if node.block:
            return placed[node.block]
        self.layout_count += 1
        cid = f"layout.{self.layout_count}"
        kids = [self.layout(child, placed) for child in node.children]
        self.add(
            {
                "id": cid,
                "component": "WorkspaceLayout",
                "kind": node.kind,
                "title": node.title,
                "description": "",
                "ratio": "balanced",
                "emphasis": "plain",
                "density": "compact",
                "children": kids,
                "labels": [self.label_of(child) for child in node.children],
            }
        )
        return cid

    def label_of(self, node: AdHocLayoutNode) -> str:
        if node.title:
            return node.title
        if node.block:
            block = next((b for b in self.plan.blocks if b.id == node.block), None)
            if block is not None:
                return block.title or block.component.value
        return node.kind.title()


def _check_titles(plan: AdHocPlan, handles: HandleIndex) -> None:
    ids = frozenset(handles) | frozenset(h.id for h in handles.values())
    titles = [("the plan", plan.title)]
    titles += [(f"block {b.id}", b.title) for b in plan.blocks]
    stack = [plan.layout]
    while stack:
        node = stack.pop()
        titles.append(("a layout heading", node.title))
        stack.extend(node.children)
    for where, text in titles:
        problems = check_prose(text, GuardContext(ids=ids)) if text else []
        if problems:
            raise BindError(
                "title_has_number",
                f"{where}: the title {text!r} may not hold a number ({'; '.join(problems)}). "
                "Write the title in words, without digits or number words.",
            )


def bind_plan(
    room: RoomService,
    viewer: str,
    plan: AdHocPlan,
    handles: HandleIndex,
    *,
    options: Mapping[str, BlockOptions] | None = None,
    origin: Origin | None = None,
    journal: Journal | None = None,
    history: bool = False,
    surface_id: str | None = None,
) -> Surface:
    """Bind a refs-only plan to the room's data and return the viewer's private surface.

    Raises ``BindError`` (a ``RoomError``) with a code and plain-words feedback when it cannot:
    an unknown handle, the wrong kind of handle, a result that is not current (unless
    ``history``), missing data, a title with a number, or a surface that fails ``a2ui.validate``.
    The outcome is journaled for the viewer.
    """
    origin = origin or Origin()
    sid = surface_id or f"ad:{viewer}:{new_id('s')}"
    try:
        messages = _bind(room, viewer, plan, handles, options or {}, origin, history, sid)
    except BindError as exc:
        _journal(journal, room, viewer, sid, origin, note=f"{exc.code}: {exc.message}")
        raise
    _journal(journal, room, viewer, sid, origin, messages=messages)
    return Surface(surface_id=sid, messages=tuple(messages))


def _bind(
    room: RoomService,
    viewer: str,
    plan: AdHocPlan,
    handles: HandleIndex,
    options: Mapping[str, BlockOptions],
    origin: Origin,
    history: bool,
    sid: str,
) -> list[dict[str, Any]]:
    _check_titles(plan, handles)
    build = _Build(room, plan, handles, options, origin, history)
    placed = {block.id: build.block(block) for block in plan.blocks}
    kids: list[str] = []
    if plan.title:
        build.add({"id": "plan.title", "component": "Text", "text": plan.title, "variant": "h2"})
        build.add({"id": "plan.by", "component": "Text", "text": origin.by(), "variant": "caption"})
        kids += ["plan.title", "plan.by"]
    kids.append(build.layout(plan.layout, placed))
    if len(build.comps) + 1 > a2ui.MAX_COMPONENTS:
        raise BindError(
            "too_large",
            f"the surface would hold {len(build.comps) + 1} components; the limit is "
            f"{a2ui.MAX_COMPONENTS}. Show fewer handles per block.",
        )
    comps = [{"id": "root", "component": "Column", "children": kids}, *build.comps]
    case = build.cases[0] if build.cases else str(room.read("room", "activeCase") or "")
    data: dict[str, Any] = {
        "meta": {
            "phase": "rest",
            "viewer": viewer,
            "adhoc": True,
            "scope": "private",
            "case": case,
            "title": plan.title,
            "origin": {
                "chat": origin.chat,
                "messageId": origin.message_id,
                "agent": origin.agent,
                "model": origin.model,
            },
            "createdSeq": room.seq,
            "bound": {"results": build.bound_results, "twin": build.bound_twin},
            "history": history,
        },
        "blocks": build.blocks,
    }
    messages = [
        a2ui.create_surface(sid),
        a2ui.update_components(sid, comps),
        a2ui.update_data(sid, data),
    ]
    try:
        a2ui.validate(messages, resolver(room, None))
    except a2ui.A2UIError as exc:
        raise BindError("invalid_surface", f"the surface was refused: {exc}") from exc
    return messages


def _journal(
    journal: Journal | None,
    room: RoomService,
    viewer: str,
    sid: str,
    origin: Origin,
    *,
    messages: list[dict[str, Any]] | None = None,
    note: str = "",
) -> None:
    """Record the validation outcome for the viewer: a digest and a count, never the data."""
    if journal is None:
        return
    payload: dict[str, Any] = {"surfaceId": sid, "ok": messages is not None}
    if messages is not None:
        blob = json.dumps(messages, sort_keys=True, default=str)
        payload["digest"] = hashlib.sha256(blob.encode()).hexdigest()[:16]
        body = cast(dict[str, Any], messages[1]["updateComponents"])
        payload["components"] = len(cast(list[Any], body["components"]))
    if note:
        payload["error"] = note[:300]
    journal.record(
        room.id,
        "A2UI",
        "adhoc.validate",
        origin.agent or "agent",
        viewer,
        payload,
        viewer=viewer,
        refs={"surfaceId": sid},
    )


# --- the deterministic plan, retry and fallback ---------------------------------------------


def deterministic_plan(handles: HandleIndex, refs: Iterable[str], *, title: str = "") -> AdHocPlan:
    """A plan from handles alone, no model: numbers as metrics, tables as charts, and inputs,
    artifacts, runs and proposals in blocks of their own, in one stack."""
    groups: dict[str, list[str]] = {}
    for ref in dict.fromkeys(refs):
        h = handles.get(ref)
        if h is None:
            continue
        if h.kind == "result":
            group = "chart" if h.output in ("json", "artifact") else "metrics"
        else:
            group = h.kind
        groups.setdefault(group, []).append(ref)
    names = {
        "metrics": AdHocComponent.METRICS,
        "input": AdHocComponent.INPUTS,
        "artifact": AdHocComponent.ARTIFACT,
        "run": AdHocComponent.JOB,
        "proposal": AdHocComponent.EVIDENCE,
    }
    blocks: list[AdHocBlock] = []
    for group, members in groups.items():
        if group == "chart":
            blocks += [
                AdHocBlock(id=f"b{len(blocks) + i + 1}", component=AdHocComponent.CHART, refs=[m])
                for i, m in enumerate(members)
            ]
        else:
            blocks.append(
                AdHocBlock(
                    id=f"b{len(blocks) + 1}",
                    component=names[group],
                    refs=members[:MAX_BLOCK_REFS],
                )
            )
    if not blocks:
        raise BindError("no_refs", "nothing to show: the plan names no usable handle")
    return AdHocPlan(
        title=title,
        layout=AdHocLayoutNode(
            kind="stack", children=[AdHocLayoutNode(kind="stack", block=b.id) for b in blocks]
        ),
        blocks=blocks,
    )


Reviser = Callable[[str], Awaitable[AdHocPlan]]


async def bind_with_retry(
    room: RoomService,
    viewer: str,
    plan: AdHocPlan,
    handles: HandleIndex,
    *,
    revise: Reviser | None = None,
    attempts: int = 2,
    options: Mapping[str, BlockOptions] | None = None,
    origin: Origin | None = None,
    journal: Journal | None = None,
    history: bool = False,
) -> Surface:
    """Bind; when the surface itself is at fault, retry with the feedback (if the caller can ask
    the model again) and then fall back to the deterministic plan. A refusal about the request
    (unknown handle, wrong kind, not current, no data) is raised, never papered over."""
    current = plan
    last: BindError | None = None
    for attempt in range(max(1, attempts)):
        try:
            return bind_plan(
                room,
                viewer,
                current,
                handles,
                options=options,
                origin=origin,
                journal=journal,
                history=history,
            )
        except BindError as exc:
            last = exc
            if exc.code not in _REPAIRABLE:
                raise
            if revise is None or attempt + 1 >= attempts:
                break
            try:
                current = await revise(exc.feedback)
            except (ValidationError, ValueError):
                break
    try:
        fallback = deterministic_plan(handles, [r for b in plan.blocks for r in b.refs])
    except BindError:
        raise (last or BindError("invalid_surface", "the plan could not be bound")) from None
    return bind_plan(
        room, viewer, fallback, handles, origin=origin, journal=journal, history=history
    )


# --- ops -------------------------------------------------------------------------------------

# Surfaces made during a turn wait here until the loop drains them as ``Surface`` events. The
# loop drains after every call, so this is short-lived; the bounds below keep it so when a turn
# ends before it drains (a crash, a cancelled request): at most ``MAX_WAITING_TURNS`` turns are
# held, each for at most ``MAX_WAITING_AGE_S`` seconds.
_SURFACES: dict[tuple[str, str, str], list[Surface]] = {}
_SURFACE_TIME: dict[tuple[str, str, str], float] = {}
MAX_WAITING_TURNS = 64
MAX_WAITING_AGE_S = 600.0


def _forget_surfaces(now: float, *, incoming: int = 0) -> None:
    """Drop surfaces nobody collected: the old ones, then the oldest beyond the size cap."""
    for key in [k for k, t in _SURFACE_TIME.items() if now - t > MAX_WAITING_AGE_S]:
        _SURFACES.pop(key, None)
        _SURFACE_TIME.pop(key, None)
    excess = len(_SURFACES) - MAX_WAITING_TURNS + incoming
    for key in sorted(_SURFACE_TIME, key=lambda k: _SURFACE_TIME[k])[: max(excess, 0)]:
        _SURFACES.pop(key, None)
        _SURFACE_TIME.pop(key, None)


def _hold_surface(key: tuple[str, str, str], surface: Surface) -> None:
    now = time.monotonic()
    _forget_surfaces(now, incoming=0 if key in _SURFACES else 1)
    _SURFACES.setdefault(key, []).append(surface)
    _SURFACE_TIME[key] = now


def take_surfaces(room_id: str, person: str, msg_id: str) -> list[Surface]:
    """The surfaces the ``ui.*`` ops made for this turn since the last call. The loop yields
    each as a ``Surface`` event, and the session journals it for this viewer only."""
    key = (room_id, person, msg_id)
    _SURFACE_TIME.pop(key, None)
    return _SURFACES.pop(key, [])


def _origin(ctx: OpContext) -> Origin:
    cfg = ctx.room.agent_config(ctx.turn.aid)
    model = " ".join(p for p in (cfg.model_provider, cfg.model_name or "") if p)
    return Origin(agent=ctx.turn.aid, model=model, message_id=ctx.turn.msg_id)


def _journal_of(ctx: OpContext) -> Journal | None:
    found = getattr(getattr(ctx.turn.kernel, "composer", None), "journal", None)
    return found if isinstance(found, Journal) else None


def _surfaces_this_turn(ctx: OpContext) -> int:
    shown = ("ui.show", "ui.chart", "ui.table")
    return sum(1 for e in ctx.turn.tool_log if e.get("ref") in shown and e.get("status") == "ok")


async def _show(
    ctx: OpContext, plan: AdHocPlan, options: Mapping[str, BlockOptions], *, history: bool = False
) -> OpResult:
    if _surfaces_this_turn(ctx) >= MAX_SURFACES_PER_TURN:
        raise RoomError(
            f"at most {MAX_SURFACES_PER_TURN} surfaces in one answer; say what you showed instead",
            code="limit",
        )
    handles = issue_handles(ctx.room, ctx.person)
    surface = await bind_with_retry(
        ctx.room,
        ctx.person,
        plan,
        handles,
        options=options,
        origin=_origin(ctx),
        journal=_journal_of(ctx),
        history=history,
    )
    _hold_surface((ctx.room.id, ctx.person, ctx.turn.msg_id), surface)
    refs = dict.fromkeys(r for b in plan.blocks for r in b.refs)
    used = tuple(handles[r].id for r in refs if r in handles and handles[r].kind == "result")
    return OpResult(
        summary=f"showed {plan.title or 'a surface'} in chat",
        data={"surfaceId": surface.surface_id, "blocks": [b.id for b in plan.blocks]},
        result_ids=used,
    )


_HANDLE_LIST: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string", "maxLength": 16},
    "maxItems": MAX_BLOCK_REFS,
}


def _layout_schema(depth: int) -> dict[str, Any]:
    props: dict[str, Any] = {
        "kind": {"type": "string", "enum": ["stack", "grid", "tabs", "section"]},
        "title": {"type": "string", "maxLength": 100, "x-prose": True},
        "block": {"type": "string", "maxLength": 32},
    }
    if depth > 1:
        props["children"] = {"type": "array", "items": _layout_schema(depth - 1), "maxItems": 12}
    return {
        "type": "object",
        "properties": props,
        "required": ["kind"],
        "additionalProperties": False,
    }


_SHOW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "maxLength": 100, "x-prose": True},
        "layout": _layout_schema(3),
        "blocks": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "maxLength": 32},
                    "component": {"type": "string", "enum": [c.value for c in AdHocComponent]},
                    "title": {"type": "string", "maxLength": 200, "x-prose": True},
                    "refs": _HANDLE_LIST,
                },
                "required": ["id", "component"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["layout", "blocks"],
    "additionalProperties": False,
}


def _plan_error(exc: ValidationError) -> RoomError:
    first = exc.errors()[0]
    where = ".".join(str(p) for p in first["loc"]) or "plan"
    return RoomError(f"{where}: {first['msg']}", code="bad_plan")


@op(
    "ui.show",
    risk=RiskClass.R1,
    min_autonomy=Autonomy.SUGGEST,
    schema=_SHOW_SCHEMA,
    writes=False,
    label="Show in chat",
    description=(
        "Show a private surface in the chat from handles listed by ui.handles. Blocks: Values, "
        "Metrics, Table, Chart, Heatmap, Inputs, Artifact, Evidence, Job, Note. "
        "Never write a number; point at handles."
    ),
)
async def ui_show(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    try:
        plan = AdHocPlan.model_validate(args)
    except ValidationError as exc:
        raise _plan_error(exc) from exc
    return await _show(ctx, plan, {})


@op(
    "ui.chart",
    risk=RiskClass.R1,
    min_autonomy=Autonomy.SUGGEST,
    schema={
        "type": "object",
        "properties": {
            "refs": {**_HANDLE_LIST, "minItems": 1},
            "x": {"type": "string", "maxLength": 80},
            "y": {"type": "array", "items": {"type": "string", "maxLength": 80}, "maxItems": 8},
            "kind": {"type": "string", "enum": ["line", "scatter", "bar"]},
            "title": {"type": "string", "maxLength": 200, "x-prose": True},
            "history": {"type": "boolean"},
        },
        "required": ["refs"],
        "additionalProperties": False,
    },
    writes=False,
    label="Chart in chat",
    description=(
        "Chart result handles in chat. x and y name columns of a json or csv result; with only "
        "number results it draws bars. history=true also draws results that are not current."
    ),
)
async def ui_chart(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    block = AdHocBlock(
        id="chart",
        component=AdHocComponent.CHART,
        title=str(args.get("title") or ""),
        refs=list(args["refs"]),
    )
    plan = AdHocPlan(layout=AdHocLayoutNode(kind="stack", block=block.id), blocks=[block])
    opts = BlockOptions(
        x=str(args["x"]) if args.get("x") else None,
        y=tuple(str(c) for c in args.get("y") or ()),
        kind=cast(ChartKind, args.get("kind") or "line"),
    )
    return await _show(ctx, plan, {block.id: opts}, history=bool(args.get("history")))


@op(
    "ui.table",
    risk=RiskClass.R1,
    min_autonomy=Autonomy.SUGGEST,
    schema={
        "type": "object",
        "properties": {
            "refs": {**_HANDLE_LIST, "minItems": 1},
            "title": {"type": "string", "maxLength": 200, "x-prose": True},
        },
        "required": ["refs"],
        "additionalProperties": False,
    },
    writes=False,
    label="Table in chat",
    description="Show result handles as a table with value, unit, stamp, status and ledger id.",
)
async def ui_table(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    block = AdHocBlock(
        id="table",
        component=AdHocComponent.TABLE,
        title=str(args.get("title") or ""),
        refs=list(args["refs"]),
    )
    plan = AdHocPlan(layout=AdHocLayoutNode(kind="stack", block=block.id), blocks=[block])
    return await _show(ctx, plan, {})


@op(
    "ui.handles",
    risk=RiskClass.R0,
    min_autonomy=Autonomy.OBSERVE,
    schema={"type": "object", "properties": {}, "additionalProperties": False},
    label="List handles",
    description="List the handles (r1, in3, a2, run1, p1) that ui.show, ui.chart, ui.table take.",
)
async def ui_handles(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    _ = args
    handles = issue_handles(ctx.room, ctx.person)
    return OpResult(
        summary=f"{len(handles)} handles", data={"handles": [h.line() for h in handles.values()]}
    )


@op(
    "studio.compose",
    risk=RiskClass.R1,
    min_autonomy=Autonomy.SUGGEST,
    schema={
        "type": "object",
        "properties": {
            "case": {"type": "string", "maxLength": 128},
            "request": {"type": "string", "maxLength": 300, "x-prose": True},
        },
        "required": ["case"],
        "additionalProperties": False,
    },
    writes=False,
    label="Compose my studio",
    description="Have the composer rebuild your own studio for a case. Once per answer.",
)
async def studio_compose(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    case = str(args["case"])
    if case not in ctx.room.case_definitions():
        raise NotFound(f"no studio for {case}")
    if any(e.get("ref") == "studio.compose" for e in ctx.turn.tool_log):
        raise RoomError("the studio is composed once per answer", code="limit")
    kernel = ctx.turn.kernel
    composer = getattr(kernel, "composer", None)
    if composer is None or not hasattr(kernel, "model"):
        raise RoomError("the studio composer is not available yet", code="not_available")
    await composer.prepare(
        ctx.room,
        case,
        ctx.person,
        kernel.model(ctx.room, ctx.turn.aid),
        request=str(args.get("request") or "") or "Assemble the workspace for this task",
    )
    key = (ctx.room.id, case, ctx.person)
    state = composer.assembly.get(key, "unprepared")
    return OpResult(
        summary=f"composed your {case} studio ({state})",
        data={"state": state, "detail": composer.plan_sources.get(key, {}).get("detail", "")},
    )


__all__ = [
    "BindError",
    "BlockOptions",
    "Handle",
    "Origin",
    "bind_plan",
    "bind_with_retry",
    "deterministic_plan",
    "issue_handles",
    "render_handles",
    "result_table",
    "take_surfaces",
]
