"""Domain-neutral workspace composition, validation and provenance-bound publication."""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

from consilith_contracts import ResultRecord
from consilith_contracts.extensions import all_extensions, optional_extension
from consilith_contracts.workspace import (
    DESCRIPTIONS,
    HEADINGS,
    LayoutNode,
    WorkspaceBlock,
    WorkspacePlan,
)

from ..domain.service import RoomService
from ..platform.errors import MODEL_ERROR_CODES, ModelError
from ..protocols import a2ui, illustrative
from ..protocols.journal import Journal
from .model_resolver import Ready, Unavailable
from .models import ModelPort, ScriptedModel
from .providers.base import error_message

log = logging.getLogger(__name__)

T = TypeVar("T")
STAMP_ORDER = ["blocked", "warning", "checking", "verified"]

# Composition calls the model, so it is limited per (room, case, viewer): this many model
# attempts in this many seconds. A preview and a cached composition cost no model call.
COMPOSE_LIMIT = 6
COMPOSE_WINDOW_S = 600.0


def resolver(room: RoomService, journal: Journal | None = None) -> a2ui.Resolver:
    def resolve(pid: str) -> bool:
        if pid.startswith("call-") and journal is not None:
            # Only a successful, validated solver result counts as provenance. The lookup is
            # by correlation id through the journal's index, not a scan of the whole journal.
            return any(
                e.get("protocol") == "MCP"
                and e.get("kind") == "tools/result"
                and "summary" in (e.get("payload") or {})
                for e in journal.by_correlation(room.id, pid)
            )
        if pid.startswith(a2ui.ILLUSTRATIVE_PREFIX):
            return illustrative.resolves(pid)
        if pid.startswith("schema:"):
            return pid.removeprefix("schema:") in room.case_definitions()
        if pid.startswith("res-"):
            return room.read("results", pid) is not None
        if pid.startswith("led-"):
            return room.read("ledger", pid) is not None
        if pid.startswith("log:"):
            return pid[4:].isdigit() and 0 < int(pid[4:]) <= room.seq
        if pid.startswith("twin:/twin/"):
            return room.read(*pid[len("twin:/") :].split("/")) is not None
        if pid.startswith(("prop-", "dec-", "warn-", "ten-")):
            area = {"prop": "proposals", "dec": "decisions", "warn": "warnings", "ten": "tensions"}[
                pid.split("-")[0]
            ]
            return room.read(area, pid) is not None
        return False

    return resolve


def wiring_index(entries: list[dict[str, Any]]) -> dict[str, int]:
    """Which journal message produced what (FR-PR-8): the last MCP result or error of each
    call, the A2A reply that answered each task, and the referee reply for each run."""
    out: dict[str, int] = {}
    for e in entries:
        payload = cast(dict[str, Any], e.get("payload") or {})
        if e.get("protocol") == "MCP" and e.get("kind") in ("tools/result", "tools/error"):
            out[str(e.get("correlationId"))] = int(e["seq"])
        elif e.get("protocol") == "A2A" and e.get("kind") == "SendMessage.result":
            if payload.get("taskId"):
                out[f"task:{payload['taskId']}"] = int(e["seq"])
            for block in cast(list[dict[str, Any]], payload.get("data") or []):
                for raw in cast(list[dict[str, Any]], block.get("entries") or []):
                    run = cast(dict[str, Any], raw.get("subject") or {}).get("runId")
                    if run:
                        out[f"review:{run}"] = int(e["seq"])
    return out


def _name_self(messages: list[dict[str, Any]], seq: int) -> list[dict[str, Any]]:
    """Fill this message's own journal seq into the wiring of what it composed itself."""
    out = copy.deepcopy(messages)
    for m in out:
        model = cast(dict[str, Any], m.get("updateDataModel") or {})
        value = cast(dict[str, Any], model.get("value") or {})
        wiring = cast(dict[str, dict[str, Any]], value.get("meta", {}).get("wiring", {}))
        result_wiring = value.get("meta", {}).get("resultWiring", {})
        for w in [*wiring.values(), *result_wiring.values()]:
            if w.get("protocol") == "A2UI" and w.get("journalSeq") is None:
                w["journalSeq"] = seq
    return out


def _q(rec: ResultRecord | None, label: str) -> dict[str, Any]:
    if rec is None:
        return a2ui.placeholder(f"{label}: not computed")
    out: dict[str, Any] = {
        "kind": rec.kind,
        "provenanceId": rec.id,
        "stamp": rec.stamp.value,
        "status": rec.status.value,
        "tier": int(rec.tier),
        "artifacts": [a.wire() for a in rec.artifacts],
    }
    if rec.kind != "artifact":
        out["value"] = rec.value
    if rec.kind == "number":
        out["v"] = rec.value
        out["unit"] = rec.unit
    if rec.illustrative:
        out["illustrative"] = True
    return out


def _provenance(rec: ResultRecord) -> dict[str, Any]:
    prov = rec.provenance
    size = ", ".join(f"{key} {value}" for key, value in prov.size.items())
    line: dict[str, Any] = {
        "provenanceId": rec.id,
        "wallTimeS": prov.wall_time_s,
        "text": " · ".join(
            part
            for part in (
                f"{prov.tool} {prov.solver_version}",
                size,
                f"wall {prov.wall_time_s:.2f} s",
            )
            if part
        ),
    }
    if rec.ledger_id:
        line["ledgerId"] = rec.ledger_id
    return line


def _spec(room: RoomService, case: str, lane: str, name: str) -> dict[str, Any]:
    node = room.read("twin", case, lane, name)
    if node is None:
        return a2ui.placeholder(f"{name}: not set")
    if isinstance(node, dict) and "v" in node:
        n = cast(dict[str, Any], node)
        return a2ui.quantity(
            float(n["v"]), str(n.get("unit", "")), f"twin:/twin/{case}/{lane}/{name}"
        )
    return {"text": str(node)}


def _owner(room: RoomService, case: str, lane: str) -> str:
    """The name of a lane's owner, for labels that say whose lane a value comes from."""
    pid = room.read("lanes", case, lane)
    name = room.read("room", "people", pid, "name") if pid else None
    return str(name) if name else "the lane owner"


def _worst(*stamps: str | None) -> str:
    present = [s for s in stamps if s]
    return min(present, key=STAMP_ORDER.index) if present else "checking"


def latest_failure(runs: dict[str, dict[str, Any]], case: str) -> dict[str, Any] | None:
    """The case's failed run to show, only when it is the latest finished run: a failure that a
    later run got past is history (found live: a timed-out Adaption call stayed on screen after
    the result arrived)."""
    finished = [
        r
        for r in runs.values()
        if r.get("case") == case and r.get("status") in ("done", "late", "failed", "timeout")
    ]
    if finished and finished[-1].get("status") in ("failed", "timeout"):
        return finished[-1]
    return None


class Composer:
    def __init__(self, journal: Journal, clock: Callable[[], float] = time.monotonic) -> None:
        self.journal = journal
        self.clock = clock
        self.attempts: dict[tuple[str, str, str], list[float]] = {}
        self.plans: dict[tuple[str, str, str], WorkspacePlan] = {}
        self.plan_sources: dict[tuple[str, str, str], dict[str, str]] = {}
        self.plan_contexts: dict[tuple[str, str, str], str] = {}
        self.attempted_contexts: dict[tuple[str, str, str], str] = {}
        self.assembly: dict[tuple[str, str, str], str] = {}
        self.revealed: dict[tuple[str, str, str], int] = {}
        # What the composer actually did so far, in order, for the studio's progress log.
        self.progress: dict[tuple[str, str, str], list[str]] = {}
        self.execution: dict[tuple[str, str], str] = {}
        # One workspace per room and case (Hesham, 2026-10-22): whoever composes first sets the
        # layout, and every seat gets the same one with its own data and permissions, so a
        # cursor or a "look at this panel" means the same place for everyone.
        self.room_plans: dict[tuple[str, str], tuple[WorkspacePlan, dict[str, str]]] = {}
        self.locks: dict[tuple[str, str], asyncio.Lock] = {}

    def bindings(self, case: str, room: RoomService | None = None) -> dict[str, list[str]]:
        definition = (room.case_definitions() if room is not None else all_extensions("cases"))[
            case
        ]
        return {
            **{f"inputs.{lane.id}": ["SchemaForm", "ParameterGrid"] for lane in definition.lanes},
            "artifacts": ["ArtifactView"],
            "results": ["ResultTable", "MetricGrid"],
            "execution": ["JobStatus"],
            "evidence": ["EvidenceView"],
        }

    def default_plan(self, case: str, room: RoomService | None = None) -> WorkspacePlan:
        definition = (room.case_definitions() if room is not None else all_extensions("cases"))[
            case
        ]
        blocks = [
            WorkspaceBlock.model_validate(
                {"id": f"block.{key}", "component": allowed[0], "binding": key}
            )
            for key, allowed in self.bindings(case, room).items()
        ]

        def leaf(key: str, title: str = "") -> LayoutNode:
            return LayoutNode(id=f"layout.{key}", kind="block", block=f"block.{key}", title=title)

        inputs = LayoutNode(
            id="layout.inputs",
            kind="tabs" if len(definition.lanes) > 1 else "stack",
            title="Control desk",
            children=[leaf(f"inputs.{lane.id}", f"inputs.{lane.id}") for lane in definition.lanes],
        )
        return WorkspacePlan(
            title="Your workspace",
            intent="Set up your model. Keep the evidence in view.",
            blocks=blocks,
            layout=LayoutNode(
                id="layout.main",
                kind="stack",
                children=[
                    LayoutNode(
                        id="layout.focus",
                        kind="split",
                        ratio="focus-start",
                        children=[
                            LayoutNode(
                                id="layout.preview",
                                kind="section",
                                emphasis="hero",
                                title="Model space",
                                children=[leaf("artifacts")],
                            ),
                            inputs,
                        ],
                    ),
                    leaf("execution"),
                    LayoutNode(
                        id="layout.review",
                        kind="split",
                        ratio="focus-start",
                        children=[leaf("results"), leaf("evidence")],
                    ),
                ],
            ),
        )

    def note(self, room: RoomService, case: str, viewer: str, text: str) -> None:
        """Record one step the composer has just reached and publish it with the skeleton. Call
        it when the step happens, never ahead of time: the studio shows these lines as they are."""
        key = (room.id, case, viewer)
        self.progress.setdefault(key, []).append(text)
        self.surface(room, case, viewer, "skeleton")

    def allow_attempt(self, key: tuple[str, str, str]) -> float:
        """0 when this (room, case, viewer) may call the model now, and otherwise the seconds to
        wait. A call that is allowed is counted."""
        now = self.clock()
        recent = [t for t in self.attempts.get(key, []) if now - t < COMPOSE_WINDOW_S]
        if len(recent) >= COMPOSE_LIMIT:
            self.attempts[key] = recent
            return max(1.0, COMPOSE_WINDOW_S - (now - recent[0]))
        recent.append(now)
        self.attempts[key] = recent
        return 0.0

    async def prepare(
        self,
        room: RoomService,
        case: str,
        viewer: str,
        model: ModelPort | Ready | Unavailable,
        request: str = "",
        preview: bool = False,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Serialize composition per viewer so reconnects cannot duplicate provider calls.

        ``model`` may be what the resolver returned: ``Unavailable`` shows ``model_required``
        with the resolver's words and fix action, and ``Ready`` is unwrapped."""
        # Per room and case: a second seat waits for the first composition instead of starting
        # its own, then takes the room's layout.
        async with self.locks.setdefault((room.id, case), asyncio.Lock()):
            return await self._prepare(room, case, viewer, model, request, preview)

    async def _prepare(
        self,
        room: RoomService,
        case: str,
        viewer: str,
        model: ModelPort | Ready | Unavailable,
        request: str = "",
        preview: bool = False,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Assemble before execution; fail closed unless preview is explicitly requested."""
        import json

        key = (room.id, case, viewer)
        shared = self.room_plans.get((room.id, case))
        if shared is not None and not request and not preview:
            # The room already has its workspace: this seat gets the same one, with no model
            # call (and so no model needed). It still assembles in front of the person.
            return await self._place(room, case, viewer, *shared)
        if isinstance(model, Unavailable):
            if not preview:
                return self._unavailable(room, case, viewer, model)
            model = ScriptedModel()  # a preview needs no model
        if isinstance(model, Ready):
            model = cast(ModelPort, model.model)
        context_id = json.dumps(
            [
                model.name,
                getattr(model, "model", model.name),
                room.read("room", "title"),
                room.read("room", "agents", f"{viewer}.agent", "config", "goal"),
            ]
        )
        if (
            not request
            and not preview
            and self.attempted_contexts.get(key) == context_id
            and self.plan_sources.get(key, {}).get("kind") == "unavailable"
        ):
            return self.surface(room, case, viewer, "rest")
        if (
            key in self.plans
            and self.plan_contexts.get(key) == context_id
            and not request
            and not preview
            and (self.plan_sources[key]["kind"] == "model" or isinstance(model, ScriptedModel))
        ):
            return self.surface(room, case, viewer, "rest")
        self.attempted_contexts[key] = context_id
        self.assembly[key] = "composing"
        self.revealed[key] = 0
        self.progress[key] = []
        self.surface(room, case, viewer, "skeleton")
        bindings = self.bindings(case, room)
        context = {
            "request": request or "Assemble a complete workspace before execution",
            "capability": room.case_definitions()[case].model_dump(mode="json"),
            "bindings": bindings,
            "allowedHeadings": list(HEADINGS) + list(bindings),
            "allowedDescriptions": list(DESCRIPTIONS),
            "viewer": viewer,
            "task": room.read("room", "title"),
            "role": room.read("room", "people", viewer, "role"),
            "goal": room.read("room", "agents", f"{viewer}.agent", "config", "goal"),
            "availableInputs": room.read("twin", case),
            "brand": (
                "Consilith: Graphite on Vellum. Warm paper for people and decisions; "
                "graphite model viewport; Instrument typography; meaningful seat accents."
            ),
        }
        self.note(room, case, viewer, "Reading the capability")
        system = (
            "Compose a complete declarative workspace from the supplied capability and catalog. "
            "Design a distinctive task-driven nested layout, not a uniform dashboard grid. "
            "Use stack, split with focus-start/focus-end ratios, grid, tabs, and sections. "
            "Give the primary working area visual priority; group input lanes into "
            "labelled tabs or compact sections; keep Run accessible. "
            "Choose hierarchy and component variants from the task, artifact "
            "availability, input complexity and owner role. "
            "Variation should serve the task, never randomness. A tab leaf must "
            "have a readable title. "
            "The layout tree references each declared block exactly once; container "
            "IDs and block IDs must be distinct. "
            "Every binding must occur exactly once. Use only listed bindings and components. "
            "Titles and descriptions MUST use the exact supplied "
            "allowedHeadings/allowedDescriptions; binding names resolve to trusted "
            "labels. "
            "Never invent scientific claims, values, evidence-status prose, "
            "permissions, actions, URLs or code. "
            "The job is idle; compose all controls, artifacts, results and evidence before it runs."
        )
        source = {
            "kind": "preview" if preview else "unavailable",
            "model": getattr(model, "model", model.name),
            "detail": "Explicit descriptor preview"
            if preview
            else "Connect a model to assemble your workspace",
        }
        plan = self.default_plan(case, room)
        invalid = False
        if preview or isinstance(model, ScriptedModel):
            self.note(room, case, viewer, "Using the standard layout")
        else:
            # A layout that fails the binding check gets one more attempt with the validation
            # feedback (a JSON repair in the adapter cannot fix a wrong binding). A
            # ``ModelError`` is never retried here: the adapter already retried what it could.
            for _attempt in range(2):
                wait = self.allow_attempt(key)
                if wait:
                    source["code"] = "rate_limited"
                    source["detail"] = (
                        f"You have composed several times in a row. Try again in {wait:.0f} s."
                    )
                    break
                try:
                    self.note(
                        room, case, viewer, "Choosing controls and laying out the model space"
                    )
                    proposed = await model.structured(system, json.dumps(context), WorkspacePlan)
                    self.note(room, case, viewer, "Checking bindings")
                    proposed.check(bindings)
                    plan = proposed
                    source = {
                        "kind": "model",
                        "model": getattr(model, "model", model.name),
                        "detail": "Validated model composition",
                    }
                    break
                except ModelError as exc:
                    # One message table for every provider: the same failure reads the same.
                    source["code"] = exc.code
                    source["detail"] = (
                        error_message(exc.code)
                        if exc.code in MODEL_ERROR_CODES
                        else exc.user_message
                    )
                    if exc.fix_action:
                        source["fixAction"] = exc.fix_action
                    break
                except ValueError as exc:
                    invalid = True
                    context["validationFeedback"] = str(exc)[:500]
                    log.warning("composed workspace for %s rejected: %s", case, str(exc)[:300])
                    source["detail"] = "The model returned an invalid workspace. Try again."
                except Exception:
                    # Provider failures are not automatically retried or billed twice.
                    source["detail"] = (
                        "The model could not assemble this workspace. Check its "
                        "connection and try again."
                    )
                    break
        if source["kind"] == "unavailable" and invalid:
            # Twice a layout that does not validate: the standard layout, said plainly, rather
            # than an empty studio. The person can ask for a composition again.
            source = {
                "kind": "preview",
                "model": source["model"],
                "detail": "Standard layout: the model's own layout did not pass the checks",
            }
        if source["kind"] == "unavailable":
            self.plan_sources[key] = source
            self.assembly[key] = (
                "model_required" if isinstance(model, ScriptedModel) else "composition_failed"
            )
            self.revealed[key] = 0
            return self.surface(room, case, viewer, "rest")
        self.plan_contexts[key] = context_id
        if not preview:
            # The room's workspace from now on; seats already showing one move to it.
            self.room_plans[(room.id, case)] = (plan, source)
            for other in [k for k in self.plans if k[:2] == (room.id, case) and k != key]:
                self.plans[other] = plan
                self.plan_sources[other] = source
                if self.assembly.get(other) == "ready":
                    self.revealed[other] = len(plan.blocks)
                    self.surface(room, case, other[2], "rest")
        return await self._place(room, case, viewer, plan, source)

    async def _place(
        self,
        room: RoomService,
        case: str,
        viewer: str,
        plan: WorkspacePlan,
        source: dict[str, str],
    ) -> tuple[int, list[dict[str, Any]]]:
        """Show ``plan`` to one seat, panel by panel."""
        key = (room.id, case, viewer)
        if self.plans.get(key) is plan and self.assembly.get(key) == "ready":
            return self.surface(room, case, viewer, "rest")
        self.plans[key] = plan
        self.plan_sources[key] = source
        self.assembly[key] = "assembling"
        self.note(room, case, viewer, "Placing panels")
        for count in range(1, len(plan.blocks) + 1):
            self.revealed[key] = count
            self.surface(room, case, viewer, "materialising")
            await asyncio.sleep(0.025)
        self.assembly[key] = "ready"
        return self.surface(room, case, viewer, "rest")

    def _unavailable(
        self, room: RoomService, case: str, viewer: str, why: Unavailable
    ) -> tuple[int, list[dict[str, Any]]]:
        """The resolver says no model can run: show ``model_required`` with its words and the
        action that fixes it (for example ``reconnect``)."""
        key = (room.id, case, viewer)
        self.plan_sources[key] = {
            "kind": "unavailable",
            "model": "none",
            "detail": why.reason,
            "code": why.code,
            **({"fixAction": why.fix_action} if why.fix_action else {}),
        }
        self.assembly[key] = "model_required"
        self.revealed[key] = 0
        return self.surface(room, case, viewer, "rest")

    def compose(
        self, room: RoomService, case: str, viewer: str, phase: str
    ) -> list[dict[str, Any]]:
        definition = room.case_definitions()[case]
        key = (room.id, case, viewer)
        plan = self.plans.get(key) or self.default_plan(case, room)
        plan.check(self.bindings(case, room))
        source = self.plan_sources.get(
            key,
            {
                "kind": "pending",
                "model": "none",
                "detail": "Your agent is preparing the workspace",
            },
        )
        sid = f"{case}:{viewer}:main"
        comps: list[dict[str, Any]] = [
            {"id": "root", "component": "Column", "children": ["workspace"]},
            {
                "id": "workspace",
                "component": "Column",
                "children": [],
            },
        ]
        data: dict[str, Any] = {
            "meta": {
                "phase": phase,
                "viewer": viewer,
                "case": case,
                "assembly": self.assembly.get(key, "unprepared"),
                "composition": source,
                "workspaceTitle": plan.title,
                "workspaceIntent": plan.intent,
                "wiring": {},
                "progress": list(self.progress.get(key, [])),
            },
            "inputs": {},
        }
        current = room.current_results(case)
        capability = next(
            (item for item in (room.read("room", "capabilities") or []) if item.get("id") == case),
            {},
        )
        intake_bindings = capability.get("intakeBindings", [])
        if isinstance(intake_bindings, dict):
            intake_bindings = [
                {**binding, "kind": kind}
                for kind, binding in intake_bindings.items()
                if isinstance(binding, dict)
            ]
        reviewed: dict[str, ResultRecord] = {}
        for lane in definition.lanes:
            owner = room.read("lanes", case, lane.id)
            fields = []
            for field in lane.fields:
                raw = room.read("twin", case, lane.id, field.name)
                review_candidate = current.get(field.review_quantity or "")
                if field.review_quantity and isinstance(raw, str) and raw:
                    node = room.read("results", raw)
                    if node is not None:
                        record = ResultRecord.model_validate(node)
                        if record.case == case and record.quantity == field.review_quantity:
                            reviewed[record.id] = record
                fields.append(
                    {
                        "definition": {
                            **field.model_dump(mode="json"),
                            "provenanceId": f"schema:{case}",
                        },
                        "value": raw,
                        "path": f"/twin/{case}/{lane.id}/{field.name}",
                        "present": raw is not None,
                        "reviewCandidate": {
                            "id": review_candidate.id,
                            "status": review_candidate.status.value,
                        }
                        if review_candidate is not None and field.review_quantity
                        else None,
                        "intakeBinding": next(
                            (
                                binding
                                for binding in intake_bindings
                                if binding.get("lane") == lane.id
                                and binding.get("field") == field.name
                            ),
                            None,
                        ),
                        "provenanceId": f"twin:/twin/{case}/{lane.id}/{field.name}"
                        if raw is not None
                        else f"schema:{case}",
                    }
                )
            data["inputs"][lane.id] = {
                "title": lane.label,
                "lane": lane.id,
                "case": case,
                "owner": owner,
                "editable": owner == viewer and self.assembly.get(key) == "ready",
                "fields": fields,
                "provenanceId": f"schema:{case}",
            }
        data["results"] = {
            "title": "Results",
            "items": [
                {"name": name, "quantity": _q(rec, name), "provenance": _provenance(rec)}
                for name, rec in current.items()
            ],
        }
        runs = [
            r
            for r in cast(dict[str, dict[str, Any]], room.read("runs") or {}).values()
            if r.get("case") == case
        ]
        last = runs[-1] if runs else {}
        jobs = [job for job in (room.read("queue") or {}).values() if job.get("case") == case]
        active_jobs = [job for job in jobs if job.get("status") in ("running", "queued")]
        running = any(r.get("status") in ("running", "queued") for r in runs) or bool(active_jobs)
        latest_job = max(jobs, key=lambda job: job.get("updatedSeq", 0), default={})
        if latest_job and latest_job.get("updatedSeq", 0) >= last.get("openedSeq", 0):
            last = latest_job
        job_state = (
            "running" if any(job.get("status") == "running" for job in active_jobs) else "queued"
        )
        execution_state = (
            job_state
            if active_jobs
            else ("running" if running else str(last.get("status", "idle")))
        )
        last_error = last.get("error") or (last.get("result") or {}).get("error")
        ready = all(room.read("lanes", case, lane.id) for lane in definition.lanes)
        data["execution"] = {
            "title": definition.label,
            "case": case,
            "action": "run",
            "state": self.execution.get((room.id, case), execution_state),
            "enabled": bool(
                ready
                and not running
                and self.assembly.get(key, "unprepared") == "ready"
                and (room.id, case) not in self.execution
            ),
            "error": None if running else last_error,
            "description": definition.grammar,
        }
        old = [
            r
            for r in cast(dict[str, dict[str, Any]], room.read("results") or {}).values()
            if r.get("case") == case
        ]
        evidence_state = "absent" if not old else "stale"
        if current:
            evidence_state = (
                "verified"
                if all(r.stamp.value == "verified" for r in current.values())
                else "checking"
            )
            if any(r.stamp.value == "warning" for r in current.values()):
                evidence_state = "attention_required"
            if any(r.stamp.value == "blocked" for r in current.values()):
                evidence_state = "rejected"
        data["evidence"] = {
            "title": "Evidence",
            "state": evidence_state,
            "items": [
                {
                    "name": name,
                    "stamp": rec.stamp.value,
                    "status": rec.status.value,
                    "provenanceId": rec.id,
                    "ledgerId": rec.ledger_id,
                }
                for name, rec in current.items()
            ],
        }
        data["artifacts"] = {
            "title": "Artifacts",
            "items": [],
            "placeholder": "No artifact is available yet",
        }
        # Input artifacts are references supplied by users; no illustrative outputs are substituted.
        for lane in definition.lanes:
            for field in lane.fields:
                raw = room.read("twin", case, lane.id, field.name)
                if isinstance(raw, dict) and isinstance(raw.get("artifact"), str):
                    data["artifacts"]["items"].append(
                        {
                            "label": field.label or field.name,
                            "sha256": raw["artifact"],
                            "kind": "artifact",
                            "source": "input",
                        }
                    )
        seen_artifacts: set[str] = set()
        reviewed_runs = {rec.run_id for rec in reviewed.values()}
        # A review output can bind a run whose previews are sibling typed outputs.
        # Keep that run's artifacts visible as historical review material, without
        # promoting any sibling back to current evidence.
        for node in cast(dict[str, Any], room.read("results") or {}).values():
            record = ResultRecord.model_validate(node)
            if record.case == case and record.run_id in reviewed_runs:
                reviewed[record.id] = record
        artifact_records = [(name, rec, "computed") for name, rec in current.items()]
        artifact_records += [
            (rec.quantity, rec, "reviewed snapshot")
            for rec in reviewed.values()
            if rec.id not in {r.id for r in current.values()}
        ]
        for name, rec, source in artifact_records:
            for artifact in rec.artifacts:
                if artifact.sha256 in seen_artifacts:
                    continue
                seen_artifacts.add(artifact.sha256)
                data["artifacts"]["items"].append(
                    {
                        "label": name,
                        "sha256": artifact.sha256,
                        "kind": artifact.kind,
                        "role": artifact.role,
                        "source": source,
                        "illustrative": rec.illustrative,
                        "provenanceId": rec.id,
                    }
                )
        visible = {b.id: b for b in plan.blocks[: self.revealed.get(key, 0)]}
        labels_by_binding = {f"inputs.{lane.id}": lane.label for lane in definition.lanes}

        def render_layout(node: LayoutNode) -> str | None:
            if node.kind == "block":
                block = visible.get(node.block or "")
                if block is None:
                    return None
                comps.append(
                    {
                        "id": block.id,
                        "component": block.component,
                        "data": {
                            "path": ("/inputs/" + block.binding.removeprefix("inputs."))
                            if block.binding.startswith("inputs.")
                            else "/" + block.binding
                        },
                    }
                )
                return block.id
            child_ids: list[str] = []
            labels: list[str] = []
            for child in node.children:
                child_id = render_layout(child)
                if child_id is not None:
                    child_ids.append(child_id)
                    labels.append(labels_by_binding.get(child.title, child.title))
            if not child_ids:
                return None
            comps.append(
                {
                    "id": node.id,
                    "component": "WorkspaceLayout",
                    "kind": node.kind,
                    "title": labels_by_binding.get(node.title, node.title),
                    "description": node.description,
                    "ratio": node.ratio,
                    "emphasis": node.emphasis,
                    "density": node.density,
                    "children": child_ids,
                    "labels": labels,
                }
            )
            return node.id

        layout_root = render_layout(plan.layout)
        if layout_root:
            comps[1]["children"].append(layout_root)
        root = comps[0]["children"]
        self._shared(
            room,
            case,
            viewer,
            str(room.read("room", "people", viewer, "role") or ""),
            comps,
            root,
            data,
        )
        data["meta"]["wiring"] = self._wiring(room, comps, data)
        return [
            a2ui.create_surface(sid),
            a2ui.update_components(sid, comps),
            a2ui.update_data(sid, data),
        ]

    def _wiring(
        self, room: RoomService, comps: list[dict[str, Any]], data: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """``wiring {protocol, journalSeq}`` for every component. A value names the MCP call
        that computed it (and the referee's A2A reply for its stamp); a proposal names the A2A
        task; anything the conductor composed itself names this A2UI message, whose seq is
        filled in when the message is journaled (``surface``)."""
        index = wiring_index(self.journal.entries(room.id))
        this: dict[str, Any] = {"protocol": "A2UI", "journalSeq": None}

        def for_result(rid: str | None) -> dict[str, Any] | None:
            node = cast(dict[str, Any] | None, room.read("results", rid)) if rid else None
            if node is None:
                return None
            run = str(node.get("runId", ""))
            out: dict[str, Any] = (
                {"protocol": "MCP", "journalSeq": index[f"call-{run}"]}
                if f"call-{run}" in index
                else dict(this)  # a room-side hand calculation: no solver message
            )
            if f"review:{run}" in index:
                out["stamp"] = {"protocol": "A2A", "journalSeq": index[f"review:{run}"]}
            return out

        data["meta"]["resultWiring"] = {
            row["quantity"]["provenanceId"]: for_result(row["quantity"]["provenanceId"])
            for row in data.get("results", {}).get("items", [])
        }

        def lookup(path: str) -> Any:
            node: Any = data
            for part in path.strip("/").split("/"):
                node = node.get(part) if isinstance(node, dict) else None
            return node

        wiring: dict[str, dict[str, Any]] = {}
        for comp in comps:
            cid, kind = str(comp["id"]), str(comp["component"])
            found: dict[str, Any] | None = None
            if kind == "ValueRow":
                value = lookup(str(comp["value"]["path"]))
                node = cast(dict[str, Any], value) if isinstance(value, dict) else {}
                pid, run = node.get("provenanceId"), node.get("runId")
                if str(pid).startswith("res-"):
                    found = for_result(str(pid))
                elif run and f"call-{run}" in index:  # the failure reason: the failed call
                    found = {"protocol": "MCP", "journalSeq": index[f"call-{run}"]}
            elif "provenance" in comp:  # a panel: its headline result
                prov = lookup(str(comp["provenance"]["path"])) or {}
                found = for_result(cast(dict[str, Any], prov).get("provenanceId"))
            elif kind in ("ApprovalChip", "GhostProposal"):
                prop = room.read("proposals", cid.removeprefix("proposal."))
                task = cast(dict[str, Any], prop or {}).get("a2aTask")
                if task and f"task:{task}" in index:
                    found = {"protocol": "A2A", "journalSeq": index[f"task:{task}"]}
            elif kind == "Panel":
                run = cast(dict[str, Any], data.get("failure") or {}).get("runId")
                if run and f"call-{run}" in index:
                    found = {"protocol": "MCP", "journalSeq": index[f"call-{run}"]}
            wiring[cid] = found or dict(this)
        return wiring

    def _shared(
        self,
        room: RoomService,
        case: str,
        viewer: str,
        role: str,
        comps: list[dict[str, Any]],
        root: list[str],
        data: dict[str, Any],
    ) -> None:
        for prop in room.open_proposals():
            if prop.case != case:
                continue
            node = {
                "id": prop.id,
                "provenanceId": prop.id,
                "author": prop.author,
                "lane": f"{case}/{prop.target_lane}",
                "owner": prop.lane_owner,
                "reason": prop.reason,
                "patch": [op.wire() for op in prop.patch],
                "interruptId": prop.interrupt_id,
            }
            cid = f"proposal.{prop.id}"
            data.setdefault("proposals", {})[prop.id] = node
            if prop.lane_owner == viewer:  # the approval chip only in the owner's view (FR-MP-4)
                comps.append(
                    {
                        "id": cid,
                        "component": "ApprovalChip",
                        "proposal": {"path": f"/proposals/{prop.id}"},
                        "action": {"event": {"name": "decide", "context": {"proposalId": prop.id}}},
                    }
                )
            else:
                comps.append(
                    {
                        "id": cid,
                        "component": "GhostProposal",
                        "data": {"path": f"/proposals/{prop.id}"},
                        "variant": "waiting-for-owner",
                    }
                )
            root.append(cid)
        for wid, w in cast(dict[str, dict[str, Any]], room.read("warnings") or {}).items():
            if w.get("to") == viewer and not w.get("acknowledged") and w.get("case") == case:
                data.setdefault("warnings", {})[wid] = {**w, "provenanceId": wid}
                comps.append(
                    {
                        "id": f"warning.{wid}",
                        "component": "WarningChip",
                        "data": {"path": f"/warnings/{wid}"},
                    }
                )
                root.append(f"warning.{wid}")
        for tid, t in cast(dict[str, dict[str, Any]], room.read("tensions") or {}).items():
            if t.get("case") != case:
                continue
            data.setdefault("tensions", {})[tid] = {**t, "provenanceId": tid}
            comps.append(
                {
                    "id": f"tension.{tid}",
                    "component": "Panel",
                    "title": "Tension",
                    "stamp": {"path": f"/tensions/{tid}/status"},
                    "children": [f"front.{tid}"],
                }
            )
            comps.append(
                {
                    "id": f"front.{tid}",
                    "component": "TradeOffFront",
                    "data": {"path": f"/tensions/{tid}"},
                }
            )
            root.append(f"tension.{tid}")
        last = latest_failure(cast(dict[str, dict[str, Any]], room.read("runs") or {}), case)
        if last is not None:
            data["failure"] = {
                "text": f"{last['tool']} {last['status']}: {last.get('error', '')}",
                "runId": last.get("id"),
            }
            comps.append(
                {
                    "id": "failure",
                    "component": "Panel",
                    "title": "Solve failed",
                    "children": ["failure.reason"],
                }
            )
            comps.append(
                {
                    "id": "failure.reason",
                    "component": "ValueRow",
                    "label": "Reason",
                    "value": {"path": "/failure"},
                }
            )
            root.append("failure")
        presentation = optional_extension("presentation", case) or {}
        data["chips"] = {"items": presentation.get("nextCalculations", {}).get(role, [])}
        comps.append({"id": "next", "component": "NextCalcChips", "chips": {"path": "/chips"}})
        root.append("next")
        entries = [
            {
                "seq": r.seq,
                "kind": r.kind,
                "actor": r.actor,
                "meta": r.meta,
                "provenanceId": f"log:{r.seq}",
            }
            for r in room.store.log(room.id, after=max(0, room.seq - 20))
        ]
        data["log"] = {"entries": entries}
        comps.append({"id": "log", "component": "DecisionLog", "data": {"path": "/log"}})
        root.append("log")

    # publishing ------------------------------------------------------------------------
    def publish(self, room: RoomService, case: str, viewer: str, phase: str) -> int:
        return self.surface(room, case, viewer, phase)[0]

    def surface(
        self, room: RoomService, case: str, viewer: str, phase: str, to: str | None = None
    ) -> tuple[int, list[dict[str, Any]]]:
        """Compose, validate and journal one surface for ``viewer``, sent to ``to`` (the viewer
        itself, or the person using View as). Returns the journal seq and the messages."""
        recipient = to or viewer
        messages = self.compose(room, case, viewer, phase)
        a2ui.validate(messages, resolver(room, self.journal))
        sid = f"{case}:{viewer}:main"
        sent: list[list[dict[str, Any]]] = []

        def named(seq: int) -> dict[str, Any]:
            sent.append(_name_self(messages, seq))
            return {"messages": sent[-1]}

        refs = {"surfaceId": sid, "phase": phase}
        if recipient != viewer:
            refs["viewAs"] = viewer
        seq = self.journal.record(
            room.id,
            "A2UI",
            "surfaceUpdate",
            "conductor",
            recipient,
            viewer=recipient,
            refs=refs,
            payload_for=named,
        )
        return seq, sent[-1]

    def viewers(self, room: RoomService) -> list[str]:
        return sorted(cast(dict[str, Any], room.read("room", "people") or {}))

    async def lifecycle(
        self,
        room: RoomService,
        case: str,
        work: Callable[[], Awaitable[T]],
        rest_after_s: float = 0.6,
    ) -> T:
        """A ready workspace precedes execution; job and evidence states remain independent."""
        self.execution[(room.id, case)] = "running"
        for v in self.viewers(room):
            self.publish(room, case, v, "skeleton")
        try:
            out = await work()
        finally:
            self.execution.pop((room.id, case), None)
            for phase in ("materialising", "live"):
                for v in self.viewers(room):
                    self.publish(room, case, v, phase)
        await asyncio.sleep(rest_after_s)
        for v in self.viewers(room):
            self.publish(room, case, v, "rest")
        return out
