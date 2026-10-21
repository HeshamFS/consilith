"""Neutral execution ports used by independently distributed plugins.

Implementations receive explicit capabilities, never a room service, database, identity
object or signing key. A standalone application can implement the same ports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .agents import AgentConfig
from .results import ResultEnvelope, ResultRecord, Validity
from .room import Check


@dataclass
class Finding:
    check: Check
    severity: str


@dataclass
class ToolCallResult:
    """Transport-neutral result of a host-mediated tool invocation."""

    envelope: ResultEnvelope
    call_id: str
    wall_s: float


@dataclass
class SolveOutcome:
    runs: list[str] = field(default_factory=list[str])
    results: dict[str, ResultRecord] = field(default_factory=dict[str, ResultRecord])
    stamps: dict[str, str] = field(default_factory=dict[str, str])
    ledger: list[str] = field(default_factory=list[str])
    error: str | None = None
    wall_s: float = 0.0


class WorkflowContext(Protocol):
    """Snapshot-bound execution and evidence capabilities supplied by a host."""

    def snapshot(self, required: tuple[str, ...]) -> tuple[dict[str, Any], dict[str, str]]: ...
    def artifact(self, digest: str) -> bytes | None: ...
    def current_results(self) -> dict[str, ResultRecord]: ...
    def result(self, result_id: str) -> ResultRecord: ...
    async def run_tool(
        self,
        tool: str,
        arguments: dict[str, Any],
        tier: int,
        *,
        dependency: bool = False,
        snapshot: dict[str, str],
        **options: Any,
    ) -> tuple[str, ResultEnvelope | None, list[ResultRecord], str | None]: ...
    def formula(
        self,
        name: str,
        inputs: dict[str, float],
        validity: Validity,
        *,
        snapshot: dict[str, str],
        **options: Any,
    ) -> list[ResultRecord]: ...
    async def review(self, records: list[ResultRecord], outcome: SolveOutcome) -> SolveOutcome: ...


class ProposalContext(Protocol):
    """Read-only task state plus an ownership-gated proposal sink."""

    model: dict[str, Any]
    owners: dict[str, str | None]
    proposals: list[dict[str, Any]]

    def agent_for(self, owner: str) -> str: ...
    async def propose(
        self, agent: str, case: str, lane: str, patch: list[Any], reason: str, evidence: list[str]
    ) -> Any: ...


class PluginExecutionError(RuntimeError):
    """A host reported a failed plugin execution without producing values."""

    @property
    def message(self) -> str:
        return str(self)


class PolicyContext(ProposalContext, Protocol):
    """Version-one allowlist of host operations available to a policy worker.

    Every operation is scoped to the active room/case by the host adapter. This protocol
    contains no database, credentials, service instance or arbitrary attribute access.
    """

    warnings: list[dict[str, Any]]
    tensions: list[dict[str, Any]]
    revisions: dict[str, str]

    def config(self, aid: str) -> AgentConfig: ...
    def lessons(self, aid: str) -> list[Any]: ...
    def has_proposals(self) -> bool: ...
    def warn(self, aid: str, to: str, lane: str, message: str, evidence: list[str]) -> None: ...
    async def resolve(
        self, aid: str, owner: str, rec: ResultRecord, results: dict[str, ResultRecord]
    ) -> list[str]: ...
    def open_tension(self, values: dict[str, Any]) -> str: ...
    def update_tension(
        self, tid: str, key: str, value: Any, *, actor: str = "conductor"
    ) -> None: ...
    def workflow(self) -> WorkflowContext: ...
    async def tool(self, tool: str, args: dict[str, Any], **options: Any) -> ToolCallResult: ...
    def activity(self, aid: str, status: str, detail: str) -> Any: ...
    def public_skip(self, skip: dict[str, Any], lessons: list[Any]) -> dict[str, Any]: ...
    async def edit_or_propose(self, aid: str, lane: str, patch: list[Any], reason: str) -> bool: ...
    def ruled_out(
        self, aid: str, patch: list[Any], overrides: set[str] | None = None
    ) -> str | None: ...
    def note(self, aid: str, event: str, values: dict[str, Any]) -> None: ...
