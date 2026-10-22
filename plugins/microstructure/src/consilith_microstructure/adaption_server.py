"""MCP bridge to Adaption Labs: estimate a credit cost, then (only when confirmed) adapt.

Runs as its own Compose service, ``solver-adaption``, the only service with a route out. The
rules live in code, because the network gives the container general egress:

- the key is read from ``ADAPTION_API_KEY`` inside this service only and never logged,
  returned or put in an error;
- the client reaches ``https://api.prod.adaptionlabs.ai`` and, for one upload, the single
  presigned URL the API returned for that upload. Nothing else, and no redirect is followed;
- ``adaption.adapt`` needs ``confirmed: true``, a dataset of at most 200 rows and a fresh
  estimate within ``ADAPTION_MAX_CREDITS`` (default 50).

The SDK is ``adaption==0.15.0``. Its generated client does the API calls; the presigned PUT is
made here, through the same guarded transport and without the Authorization header.
"""

import asyncio
import hashlib
from functools import partial
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import __version__, dataset
from .envelopes import derived_envelope, file_output, json_output
from .run_tables import DISCLAIMER, RunTables

log = logging.getLogger("consilith.adaption")
# httpx logs every request URL at INFO; a presigned upload URL carries a temporary signature.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

API_HOST = "api.prod.adaptionlabs.ai"
API_URL = f"https://{API_HOST}"
ESTIMATE_TOOL = "adaption.estimate"
ADAPT_TOOL = "adaption.adapt"
TOOLS = [ESTIMATE_TOOL, ADAPT_TOOL]
MAX_ROWS = dataset.MAX_ROWS
MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_CREDITS = 50.0
DEFAULT_TIMEOUT_S = 900.0
DEFAULT_WAIT_S = 240.0
RUNNING_STATES = frozenset({"pending", "queued", "running", "processing", "adapting"})
READY_STATES = frozenset({"awaiting_input", "succeeded", "failed"})
COLUMN_MAPPING = {"prompt": "prompt", "completion": "completion"}
RECIPES = ("deduplication", "prompt_rephrase", "reasoning_traces")
DEFAULT_BLUEPRINT = (
    "You are a careful materials-science tutor. Answer from the simulation's own tables, state "
    "every number with its unit, and say that the model is hypothetical and dimensionless; "
    "never claim experimental validation."
)


class TrainingOptions(BaseModel):
    """Adaption's configuration step: what to build and how the completions should read."""

    model_config = ConfigDict(extra="forbid")
    training_type: Literal["instruction_dataset", "preference_pairs"] = "instruction_dataset"
    recipes: list[Literal["deduplication", "prompt_rephrase", "reasoning_traces"]] = Field(
        default_factory=lambda: ["deduplication", "reasoning_traces"]
    )
    length: Literal["minimal", "concise", "detailed", "extensive"] = "concise"
    # Web-search grounding; off by default, the numbers come from the run's tables.
    hallucination_mitigation: bool = False
    blueprint: str = Field(default=DEFAULT_BLUEPRINT, max_length=2000)

    def adapt_kwargs(self) -> dict[str, Any]:
        """The SDK's adapt arguments for these options (the same for estimate and run)."""
        return {
            "training_type": self.training_type,
            "recipe_specification": {"recipes": {r: r in self.recipes for r in RECIPES}},
            "brand_controls": {
                "length": self.length,
                "hallucination_mitigation": self.hallucination_mitigation,
                **({"blueprint": self.blueprint} if self.blueprint.strip() else {}),
            },
        }
VENDOR_NOTE = (
    "Adaption's score rates the quality of the text rows. It is not a check of the physics."
)
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SECRET = re.compile(r"pt_live_[A-Za-z0-9_-]+")
_QUERY = re.compile(r"(https?://[^\s\"'?]+)\?[^\s\"']*")
# Poll pacing; tests set these to zero.
POLL_INITIAL_S = 2.0
POLL_MAX_S = 30.0


class BridgeError(ToolError):
    """A refusal or failure whose message is safe to show: it never holds the key.

    A ``ToolError`` reaches the model and the run as the tool's message; any other exception
    is hidden behind a generic one."""


class EgressRefused(Exception):
    """The guarded transport refused a request to a host or URL that is not allowed."""


def redact(text: str, key: str | None = None) -> str:
    """``text`` without the key, key-shaped tokens or URL query strings."""
    if key:
        text = text.replace(key, "[key]")
    return _QUERY.sub(r"\1?[query]", _SECRET.sub("[key]", text))[:400]


class EgressGuard(httpx.BaseTransport):
    """Allow only the API host, plus the exact upload URLs registered for the next calls."""

    def __init__(self, inner: httpx.BaseTransport | None = None) -> None:
        self.inner = inner or httpx.HTTPTransport()
        self._uploads: set[tuple[str, str]] = set()
        self.refused: list[str] = []

    def allow_upload(self, url: str) -> None:
        """Allow one presigned HTTPS URL (host and path) for the one PUT that follows."""
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise BridgeError("Adaption returned an upload URL that is not HTTPS")
        self._uploads.add((parts.hostname.lower(), parts.path))
        log.info("upload host allowed: %s", parts.hostname.lower())

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = (request.url.host or "").lower()
        port_ok = request.url.port in (None, 443)
        allowed = (
            request.url.scheme == "https"
            and port_ok
            and (host == API_HOST or (host, request.url.path) in self._uploads)
        )
        if not allowed:
            self.refused.append(host)
            raise EgressRefused(f"refused a request to host {host!r}")
        if host != API_HOST:
            self._uploads.discard((host, request.url.path))  # one use
        return self.inner.handle_request(request)

    def close(self) -> None:
        self.inner.close()


def _number(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return default
    return value if value > 0 else default


def max_credits() -> float:
    return _number("ADAPTION_MAX_CREDITS", DEFAULT_MAX_CREDITS)


def timeout_s() -> float:
    return _number("ADAPTION_TIMEOUT_S", DEFAULT_TIMEOUT_S)


def wait_s() -> float:
    """How long one adapt call waits for Adaption before it reports the run as still going
    (well inside the room's per-call deadline). A later check fetches the result."""
    return min(_number("ADAPTION_WAIT_S", DEFAULT_WAIT_S), timeout_s())


@dataclass
class Session:
    """One tool call's client. Built per call so the key is read when needed."""

    guard: EgressGuard
    key: str = field(repr=False)
    api: Any = None
    put_http: Any = None
    sdk_http: Any = None

    def close(self) -> None:
        for client in (self.api, self.sdk_http, self.put_http):
            if client is not None:
                client.close()


def open_session(inner: httpx.BaseTransport | None) -> Session:
    key = os.environ.get("ADAPTION_API_KEY", "").strip()
    if not key:
        raise BridgeError("the Adaption key is not configured on this service (ADAPTION_API_KEY)")
    from adaption import Adaption

    guard = EgressGuard(inner)
    sdk_http = httpx.Client(transport=guard, follow_redirects=False, timeout=60.0)
    put_http = httpx.Client(transport=guard, follow_redirects=False, timeout=120.0)
    api = Adaption(api_key=key, base_url=API_URL, http_client=sdk_http)
    return Session(guard, key, api, put_http, sdk_http)


def safe_error(exc: BaseException, session: Session | None) -> BridgeError:
    """Turn any failure into a ``BridgeError`` that cannot leak the key or a signed URL."""
    if isinstance(exc, BridgeError):
        return exc
    key = session.key if session else None
    if session and session.guard.refused:
        return BridgeError(f"refused a request to host {session.guard.refused[-1]!r}")
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        extra = "; redirects are refused" if 300 <= status < 400 else ""
        return BridgeError(f"Adaption returned HTTP {status}{extra}")
    name = type(exc).__name__
    if name in {"DatasetTimeout", "PollTimeout"}:
        return BridgeError(redact(str(exc), key))
    return BridgeError(f"{name}: {redact(str(exc), key)}")


def _wait(get_status: Any, dataset_id: str, deadline: float, terminal: frozenset[str]) -> Any:
    from adaption.lib.polling import DatasetTimeout, poll_until_terminal

    remaining = max(0.0, deadline - time.monotonic())
    try:
        return poll_until_terminal(
            get_status,
            dataset_id,
            initial_interval=POLL_INITIAL_S,
            max_interval=POLL_MAX_S,
            timeout=remaining,
            terminal_statuses=terminal,
            timeout_error=DatasetTimeout,
        )
    except DatasetTimeout as exc:
        raise BridgeError(
            f"timed out after {timeout_s():.0f} s waiting for dataset {dataset_id} "
            f"(last status: {exc.last_status}). It keeps running on Adaption's side."
        ) from None


def _quote(session: Session, dataset_id: str, rows: int, options: TrainingOptions) -> Any:
    return session.api.datasets.adapt(
        dataset_id,
        column_mapping=COLUMN_MAPPING,
        job_specification={"max_rows": rows},
        estimate=True,
        **options.adapt_kwargs(),
    )


def _estimate_sync(
    session: Session, payload: bytes, rows: int, options: TrainingOptions
) -> dict[str, Any]:
    api = session.api
    created = api.datasets.create(
        source={"name": "microstructure-run.jsonl", "file_format": "jsonl"}
    )
    instructions = created.upload_instructions
    if instructions is None or instructions.method.upper() != "PUT":
        raise BridgeError("Adaption did not return a PUT upload instruction")
    session.guard.allow_upload(instructions.url)
    put = session.put_http.put(instructions.url, content=payload)
    put.raise_for_status()
    api.datasets.upload.complete_by_id(
        created.dataset_id,
        file_size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    deadline = time.monotonic() + timeout_s()
    state = _wait(api.datasets.get_status, created.dataset_id, deadline, READY_STATES)
    if state.status == "failed":
        message = getattr(state.error_data, "message", None) or "the upload failed"
        raise BridgeError(f"Adaption could not read the dataset: {redact(message, session.key)}")
    quote = _quote(session, created.dataset_id, rows, options)
    credits = float(quote.estimated_credits_consumed)
    return {
        "datasetId": created.dataset_id,
        "rows": rows,
        "estimatedCredits": credits,
        "estimatedMinutes": float(quote.estimated_minutes),
        "maxCredits": max_credits(),
        "withinLimit": credits <= max_credits(),
        "uploadSha256": hashlib.sha256(payload).hexdigest(),
        "nothingSpent": True,
        "options": options.model_dump(),
    }


def _evaluation(session: Session, dataset_id: str, deadline: float) -> dict[str, Any]:
    from adaption.lib.polling import EVALUATION_TERMINAL_STATUSES

    base = {"datasetId": dataset_id, "note": VENDOR_NOTE}
    try:
        result = _wait(
            session.api.datasets.get_evaluation,
            dataset_id,
            deadline,
            EVALUATION_TERMINAL_STATUSES,
        )
    except BridgeError as exc:
        return {**base, "skipped": True, "status": "timeout", "reason": str(exc)}
    quality = result.quality
    if result.status != "succeeded" or quality is None:
        return {
            **base,
            "skipped": True,
            "status": result.status or "unknown",
            "reason": f"Adaption reported the evaluation as {result.status or 'unavailable'}",
        }
    keys = (
        "score_before",
        "score_after",
        "grade_before",
        "grade_after",
        "percentile_after",
        "improvement_percent",
    )
    return {
        **base,
        "skipped": False,
        "status": "succeeded",
        **{k: getattr(quality, k) for k in keys},
    }


def evaluation_table(evaluation: dict[str, Any]) -> dict[str, Any]:
    columns = ["Measure", "Before", "After"]
    if evaluation.get("skipped"):
        return {
            "columns": columns,
            "rows": [["Evaluation", "skipped", str(evaluation.get("reason", ""))]],
        }
    return {
        "columns": columns,
        "rows": [
            ["Quality score (0 to 10)", evaluation["score_before"], evaluation["score_after"]],
            ["Grade (A to E)", evaluation["grade_before"], evaluation["grade_after"]],
            ["Percentile", None, evaluation["percentile_after"]],
            ["Improvement (%)", None, evaluation["improvement_percent"]],
        ],
    }


def _adapt_sync(
    session: Session,
    dataset_id: str,
    expected_credits: float | None,
    options: TrainingOptions,
    *,
    resume: bool = False,
) -> tuple[dict[str, Any] | None, bytes | None, dict[str, Any]]:
    """Start (or, with ``resume``, only check) the paid adaptation. The run is started once
    (idempotency key per dataset); a run still going after ``wait_s()`` is reported as running,
    and a later ``resume`` call fetches its evaluation and the adapted rows."""
    api = session.api
    status = api.datasets.get_status(dataset_id)
    rows = status.row_count
    if rows is None or rows > MAX_ROWS:
        raise BridgeError(f"refused: the dataset has {rows} rows; the limit is {MAX_ROWS}")
    spent: dict[str, Any] = {
        "datasetId": dataset_id,
        "rows": rows,
        "options": options.model_dump(),
    }
    if not resume:
        quote = _quote(session, dataset_id, rows, options)
        credits = float(quote.estimated_credits_consumed)
        if credits > max_credits():
            raise BridgeError(
                f"refused: the estimate of {credits:g} credits is over the limit of "
                f"{max_credits():g} (ADAPTION_MAX_CREDITS)"
            )
        if expected_credits is not None and credits > expected_credits + 1e-9:
            raise BridgeError(
                f"refused: the price is now {credits:g} credits, above the "
                f"{expected_credits:g} the person approved; estimate again"
            )
        key = hashlib.sha256(f"consilith-adapt:{dataset_id}".encode()).hexdigest()[:40]
        started = api.datasets.adapt(
            dataset_id,
            column_mapping=COLUMN_MAPPING,
            job_specification={"max_rows": rows, "idempotency_key": key},
            **options.adapt_kwargs(),
        )
        spent.update({"runId": started.run_id, "estimatedCredits": credits})
    deadline = time.monotonic() + (wait_s() if not resume else 5.0)
    try:
        done = _wait(api.datasets.get_status, dataset_id, deadline, frozenset({"succeeded", "failed"}))
    except BridgeError:
        return None, None, {**spent, "status": "running", "checkAgain": True}
    if done.status == "failed":
        message = getattr(done.error_data, "message", None) or "the adaptation failed"
        raise BridgeError(f"Adaption failed the run: {redact(message, session.key)}")
    evaluation = _evaluation(session, dataset_id, time.monotonic() + wait_s())
    raw = api.datasets.download(dataset_id, file_format="jsonl").read()
    if len(raw) > MAX_DOWNLOAD_BYTES:
        raise BridgeError("the adapted dataset is larger than the 16 MiB artifact limit")
    return evaluation, raw, {**spent, "status": "succeeded"}


def _rows_from(
    trajectory: dict[str, Any], parameters: dict[str, Any] | None
) -> list[dict[str, str]]:
    return dataset.build_rows(RunTables.from_trajectory(trajectory, parameters))


def build_server(transport: httpx.BaseTransport | None = None) -> MCPServer:
    """The MCP server. ``transport`` replaces the network in tests; it still sits behind the
    egress guard."""
    server = MCPServer(
        "consilith-microstructure-adaption",
        version=__version__,
        instructions=(
            "Estimate first; adapt only after a person confirms the credit cost. "
            "Scores rate text quality, not physics."
        ),
    )

    async def estimate(
        trajectory: dict[str, Any],
        parameters: dict[str, Any] | None = None,
        expected_sha256: str = "",
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Upload the run's training set and return Adaption's credit estimate for the chosen
        options (instruction dataset or preference pairs, recipes, length, blueprint). Spends
        nothing."""
        started = time.perf_counter()
        session = None
        try:
            chosen = TrainingOptions.model_validate(options or {})
            rows = _rows_from(trajectory, parameters)
            payload = dataset.to_jsonl(rows)
            if expected_sha256 and expected_sha256 != hashlib.sha256(payload).hexdigest():
                raise BridgeError("the training set no longer matches the one that was built")
            session = open_session(transport)
            result = await asyncio.to_thread(_estimate_sync, session, payload, len(rows), chosen)
        except Exception as exc:
            raise safe_error(exc, session) from None
        finally:
            if session:
                session.close()
        return derived_envelope(
            ESTIMATE_TOOL,
            {"trajectory": trajectory, "parameters": parameters, "options": chosen.model_dump()},
            [json_output("adaption_estimate", result)],
            started,
            validity="Credit quote for an illustrative text dataset; nothing was spent",
            notes=[DISCLAIMER, VENDOR_NOTE],
        )

    async def adapt(
        dataset_id: str,
        confirmed: StrictBool = False,
        expected_credits: float | None = None,
        options: dict[str, Any] | None = None,
        resume: StrictBool = False,
    ) -> dict[str, Any]:
        """Adapt and evaluate an estimated dataset. Spends credits; needs confirmed=true."""
        started = time.perf_counter()
        if confirmed is not True:
            raise BridgeError("refused: adaption.adapt spends credits and needs confirmed=true")
        if not _ID.match(dataset_id or ""):
            raise BridgeError("refused: dataset_id is not a valid id")
        session = None
        try:
            chosen = TrainingOptions.model_validate(options or {})
            session = open_session(transport)
            evaluation, raw, spent = await asyncio.to_thread(
                partial(_adapt_sync, resume=resume),
                session,
                dataset_id,
                expected_credits,
                chosen,
            )
        except Exception as exc:
            raise safe_error(exc, session) from None
        finally:
            if session:
                session.close()
        return derived_envelope(
            ADAPT_TOOL,
            {
                "dataset_id": dataset_id,
                "confirmed": True,
                "expected_credits": expected_credits,
                "options": chosen.model_dump(),
                "resume": resume,
            },
            [json_output("adaption_spend", spent)]
            if evaluation is None or raw is None
            else [
                json_output("adaption_evaluation", evaluation),
                json_output("adaption_evaluation_table", evaluation_table(evaluation)),
                file_output("adaption_adapted_dataset", raw, "application/jsonl", "dataset"),
                json_output("adaption_spend", spent),
            ],
            started,
            validity="Adapted text dataset and Adaption's text-quality score; not physics",
            notes=[DISCLAIMER, VENDOR_NOTE],
        )

    server.tool(name=ESTIMATE_TOOL)(estimate)
    server.tool(name=ADAPT_TOOL)(adapt)
    return server


def app():
    return build_server().streamable_http_app(
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=["127.0.0.1:*", "localhost:*", "solver-adaption:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        ),
    )
