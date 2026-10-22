"""The referee: an A2A 1.0 agent owned by nobody. It checks results with its own solver calls
and signs a ledger entry for each. Only it holds the private key.

Only the room server may ask for a review: calls must carry the shared referee token. Before
signing, the referee re-digests the manifest it was sent and refuses any item whose manifest
digest or revisions do not match it, so a signature always covers the inputs it checked.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import threading
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, cast

import uvicorn
from a2a.auth.user import User
from a2a.helpers import new_task_from_user_message
from a2a.helpers.proto_helpers import get_data_parts, new_data_part
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentInterface,
    AgentSkill,
    Message,
    Part,
    Role,
)
from consilith_contracts import (
    Check,
    LedgerEntry,
    LedgerSubject,
    ResultEnvelope,
    ReviewItem,
    ReviewRequest,
    digest,
    new_id,
)
from consilith_contracts.execution import Finding
from consilith_contracts.extensions import (
    all_extensions,
    configure_extension_loader,
    load_extensions,
    package_for,
    registry_operation,
)
from consilith_contracts.results import result_binding_digest
from consilith_contracts.review import LEDGER_MEDIA_TYPE, REVIEW_MEDIA_TYPE
from consilith_contracts.signing import (
    key_id,
    load_private,
    new_private_key,
    private_pem,
    public_pem,
    sign_subject,
)
from consilith_contracts.units import parse
from consilith_plugin_runtime import load as load_plugin
from consilith_plugin_runtime.catalog import read_catalog
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from google.protobuf import struct_pb2
from mcp import Client
from starlette.requests import Request

from .checks import stamp_for

log = logging.getLogger("consilith.referee")
VERSION = "0.1.0"
DEFAULT_CHECK_TIMEOUT_S = 60.0
DEFAULT_CHECK_CEILING_S = 3600.0
# The checker's solver calls for one review. The room signs the declared deadline into the run
# manifest (``context.deadlineS``); the referee honours it under its own operator ceiling.
_call_timeout_s: ContextVar[float | None] = ContextVar("referee_call_timeout_s", default=None)


def review_timeout_s(
    manifest_context: dict[str, Any],
    base_s: float = DEFAULT_CHECK_TIMEOUT_S,
    ceiling_s: float = DEFAULT_CHECK_CEILING_S,
) -> float:
    """The per-call timeout for one review: the base, raised to a valid declared deadline, never
    above ``ceiling_s``. A missing, non-numeric or non-positive declaration leaves the base."""
    declared = manifest_context.get("deadlineS")
    wanted = base_s
    if isinstance(declared, int | float) and not isinstance(declared, bool) and declared > 0:
        wanted = max(base_s, float(declared))
    return min(wanted, max(ceiling_s, base_s))


def load_or_create_key(path: Path) -> Ed25519PrivateKey:
    """The referee's key lives only with the referee. Its public half is written next to it
    so the room can pin it."""
    if path.exists():
        return load_private(path.read_bytes())
    key = new_private_key()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # owner-only, never overwrite
    with os.fdopen(fd, "wb") as fh:
        fh.write(private_pem(key))
    path.with_suffix(".pub.pem").write_bytes(public_pem(key.public_key()))
    return key


class SolverCaller:
    """The referee's own MCP client. It accepts only valid result envelopes."""

    def __init__(
        self,
        servers: dict[str, str],
        timeout_s: float = DEFAULT_CHECK_TIMEOUT_S,
        ceiling_s: float = DEFAULT_CHECK_CEILING_S,
    ) -> None:
        self.servers = servers
        self.timeout_s = timeout_s
        self.ceiling_s = ceiling_s
        self._tools: dict[str, str] = {}
        self._versions: dict[str, str | None] = {}

    async def __call__(self, tool: str, args: dict[str, Any]) -> ResultEnvelope:
        if tool not in self._tools:
            discovered: dict[str, str] = {}
            versions: dict[str, str | None] = {}
            for server, endpoint in self.servers.items():
                try:
                    async with asyncio.timeout(self.timeout_s), Client(endpoint) as client:
                        listed = await client.list_tools()
                        versions[server] = (
                            client.server_info.version if client.server_info else None
                        )
                except Exception as exc:
                    log.warning(
                        "checker discovery unavailable for %s: %s", server, type(exc).__name__
                    )
                    continue
                for item in listed.tools:
                    if item.name in discovered:
                        raise ValueError(f"ambiguous checker tool {item.name!r}")
                    discovered[item.name] = server
            self._tools = discovered
            self._versions = versions
        if tool not in self._tools:
            raise ValueError(f"checker tool unavailable: {tool}")
        server = self._tools[tool]
        url = self.servers[server]
        timeout_s = _call_timeout_s.get() or self.timeout_s
        async with asyncio.timeout(timeout_s), Client(url) as client:
            current = client.server_info.version if client.server_info else None
            expected = self._versions.get(server)
            if expected is not None and current != expected:
                raise RuntimeError("checker solver implementation changed after discovery")
            result = await client.call_tool(tool, args)
        if result.is_error:
            text = " ".join(getattr(c, "text", "") for c in result.content)
            raise RuntimeError(f"{tool} failed: {text}")
        env = ResultEnvelope.model_validate(result.structured_content or {})
        for quantity in env.quantities:
            parse(quantity.unit)
        return env


class RoomCaller(User):
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return "room"


class SharedTokenContext(DefaultServerCallContextBuilder):
    """Only the room server, holding the shared referee token, may call the referee."""

    def __init__(self, token: str) -> None:
        self.token = token

    def build(self, request: Request) -> ServerCallContext:
        ctx = super().build(request)
        auth = request.headers.get("authorization", "")
        ok = bool(self.token) and hmac.compare_digest(
            auth.encode(), f"Bearer {self.token}".encode()
        )
        headers = {
            k: v for k, v in ctx.state.get("headers", {}).items() if k.lower() != "authorization"
        }
        ctx.state.update({"headers": headers, "caller": "room" if ok else None})
        if ok:
            ctx.user = RoomCaller()
        return ctx


def caller_scope(ctx: ServerCallContext) -> str:
    return "room" if ctx.state.get("caller") == "room" else "anonymous"


class Referee:
    def __init__(self, key: Ed25519PrivateKey, call: SolverCaller) -> None:
        self.key = key
        self.kid = key_id(key.public_key())
        self.call = call
        self._catalog_revision: str | None = None
        self._catalog_lock = threading.Lock()
        self._check_slots = asyncio.Semaphore(4)
        self._capture_checkers()

    @registry_operation
    def _capture_checkers(self) -> None:
        # These are the operator-selected checker implementations, captured independently
        # of producer-supplied profiles and model content.
        self.checkers = all_extensions("checkers")
        self.tool_checkers = all_extensions("tool_checkers")
        self.checker_pins = {name: package_for("checkers", name) for name in self.checkers}
        self.tool_checker_pins = {
            name: package_for("tool_checkers", name) for name in self.tool_checkers
        }
        self._checker_bundle = (
            self.checkers,
            self.tool_checkers,
            self.checker_pins,
            self.tool_checker_pins,
        )

    def refresh_plugins(self) -> None:
        if not os.environ.get("CONSILITH_PLUGIN_CATALOG"):
            return
        with self._catalog_lock:
            catalog = read_catalog()
            if catalog["revision"] == self._catalog_revision:
                return
            os.environ["CONSILITH_PLUGIN_TOKENS"] = json.dumps(catalog["tokens"])
            configure_extension_loader(load_plugin)
            load_extensions(catalog["references"])
            self.call.servers = dict(catalog["solver_urls"])
            self.call._tools.clear()
            self.call._versions.clear()
            self._capture_checkers()
            self._catalog_revision = catalog["revision"]

    async def review(self, req: ReviewRequest) -> list[LedgerEntry]:
        await asyncio.to_thread(self.refresh_plugins)
        checkers, tool_checkers, checker_pins, tool_checker_pins = self._checker_bundle
        check_case = tool_checkers.get(req.manifest.tool)
        checker_pin = tool_checker_pins.get(req.manifest.tool)
        if check_case is None:
            check_case = checkers.get(req.case)
            checker_pin = checker_pins.get(req.case)
        manifest_digest = digest(req.manifest)
        entries: list[LedgerEntry] = []
        for item in req.items:
            if (
                item.manifest_digest != manifest_digest
                or item.input_revisions != req.manifest.input_revisions
            ):
                raise ValueError(
                    f"{item.result_id}: its manifest digest or revisions do not match "
                    "the manifest sent"
                )
            if (
                item.artifacts or item.kind != "number"
            ) and item.result_digest != result_binding_digest(
                item.quantity,
                item.value,
                item.unit,
                req.run_id,
                item.manifest_digest,
                item.artifacts,
                kind=item.kind,
            ):
                raise ValueError("result artifacts do not match the signed result binding")

        async def inspect_item(item: ReviewItem) -> tuple[list[Finding], str]:
            if check_case is None:
                findings = [
                    Finding(
                        Check(
                            name="coverage",
                            outcome="fail",
                            detail="no approved checker is installed for this capability",
                        ),
                        "block",
                    )
                ]
                method = "no approved checker"
            else:
                async with self._check_slots:
                    findings, method = await check_case(req, item, self.call)
            return findings, method

        _call_timeout_s.set(
            review_timeout_s(
                req.manifest.context,
                getattr(self.call, "timeout_s", DEFAULT_CHECK_TIMEOUT_S),
                getattr(self.call, "ceiling_s", DEFAULT_CHECK_CEILING_S),
            )
        )
        checked = await asyncio.gather(
            *(inspect_item(item) for item in req.items),
            return_exceptions=True,
        )
        # Complete the bounded checks before signing anything. A failed checker cannot
        # leave a partially signed response, and gather preserves the requested order.
        for result in checked:
            if isinstance(result, BaseException):
                raise result
        findings_by_item = cast(list[tuple[list[Finding], str]], checked)
        for item, (findings, method) in zip(req.items, findings_by_item, strict=True):
            subject = LedgerSubject(
                room=req.room,
                case=req.case,
                run_id=req.run_id,
                result_id=item.result_id,
                result_digest=item.result_digest,
                input_revisions=item.input_revisions,
                manifest_digest=item.manifest_digest,
                quantity=item.quantity,
                kind=item.kind,
                value=item.value,
                artifacts=item.artifacts,
                unit=item.unit,
                checks=[f.check for f in findings],
                stamp=stamp_for(findings),
                method=method,
                checker_pin=checker_pin,
            )
            entries.append(
                LedgerEntry(
                    id=new_id("led"),
                    subject=subject,
                    key_id=self.kid,
                    signature=sign_subject(self.key, subject),
                    ts=time.time(),
                )
            )
        return entries


def _refusal(text: str) -> Message:
    import uuid

    return Message(role=Role.ROLE_AGENT, message_id=str(uuid.uuid4()), parts=[Part(text=text)])


class RefereeExecutor(AgentExecutor):
    def __init__(self, referee: Referee) -> None:
        self.referee = referee

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        msg = context.message
        if msg is None:
            return
        call = context.call_context
        if call is None or call.state.get("caller") != "room":
            await event_queue.enqueue_event(
                _refusal("refused: only the room server may ask for a review")
            )
            return
        task = context.current_task or new_task_from_user_message(msg)
        await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        await updater.start_work(updater.new_agent_message(parts=[Part(text="checking")]))
        try:
            data = get_data_parts(list(msg.parts))
            if not data:
                raise ValueError(f"expected a {REVIEW_MEDIA_TYPE} data part")
            entries = await self.referee.review(ReviewRequest.model_validate(data[0]))
        except Exception as exc:
            log.exception("review failed")
            text = f"review failed: {type(exc).__name__}: {exc}"
            await updater.failed(updater.new_agent_message(parts=[Part(text=text)]))
            return
        part = new_data_part({"entries": [e.wire() for e in entries]}, media_type=LEDGER_MEDIA_TYPE)
        await updater.add_artifact(parts=[part], name="ledger", last_chunk=True)
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        call = context.call_context
        if call is None or call.state.get("caller") != "room":
            raise PermissionError("only the room server may cancel a review")
        updater = TaskUpdater(event_queue, context.task_id or "", context.context_id or "")
        await updater.cancel()


def _skill() -> list[AgentSkill]:
    skills = []
    for skill_id, source in all_extensions("referee_skill_documents").items():
        text = str(source.get("body", "")) if isinstance(source, dict) else str(source)
        desc = text.split("description:", 1)[-1].split("metadata:", 1)[0].replace(">-", "").strip()
        skills.append(
            AgentSkill(
                id=skill_id,
                name=skill_id,
                description=" ".join(desc.split()),
                tags=sorted(all_extensions("checkers")),
            )
        )
    return skills


def card(public_url: str) -> AgentCard:
    profile = struct_pb2.Struct()
    profile.update(
        {
            "owner": None,
            "kind": "room",
            "lanes": [],
            "tools": sorted(all_extensions("checker_tools")),
            "permissions": {"edits": [], "blocksStamps": True, "overrulesPeople": False},
            "cases": sorted(all_extensions("checkers")),
            "memory": {"lessons": 0},
        }
    )
    return AgentCard(
        name="referee",
        description="Room agent owned by nobody. Checks units, convergence, validity and "
        "agreement with "
        "an independent method, stamps numbers and writes the ledger. It can block a stamp, never "
        f"overrule a person. Implemented cases: {', '.join(sorted(all_extensions('checkers')))}.",
        version=VERSION,
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            extensions=[
                AgentExtension(
                    uri="https://consilith.local/a2a/ext/agent-profile/v1",
                    description="Consilith agent profile",
                    required=False,
                    params=profile,
                )
            ],
        ),
        default_input_modes=[REVIEW_MEDIA_TYPE],
        default_output_modes=[LEDGER_MEDIA_TYPE],
        skills=_skill(),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC", protocol_version="1.0", url=f"{public_url}/a2a/jsonrpc"
            )
        ],
    )


def create_app(
    key: Ed25519PrivateKey,
    public_url: str,
    servers: dict[str, str] | None = None,
    *,
    token: str,
    check_ceiling_s: float = DEFAULT_CHECK_CEILING_S,
) -> FastAPI:
    agent_card = card(public_url)
    caller = SolverCaller(dict(servers) if servers is not None else {})
    caller.ceiling_s = check_ceiling_s
    handler = DefaultRequestHandler(
        agent_executor=RefereeExecutor(Referee(key, caller)),
        task_store=InMemoryTaskStore(owner_resolver=caller_scope),
        agent_card=agent_card,
    )
    app = FastAPI(title="Consilith referee", version=VERSION)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "keyId": key_id(key.public_key())}

    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card=agent_card),
        jsonrpc_routes=create_jsonrpc_routes(
            request_handler=handler,
            rpc_url="/a2a/jsonrpc",
            context_builder=SharedTokenContext(token),
        ),
    )
    return app


def main() -> None:
    import json

    from consilith_contracts.extensions import configure_extension_loader, load_extensions
    from consilith_plugin_runtime import load

    configure_extension_loader(load)

    load_extensions(json.loads(os.environ.get("CONSILITH_EXTENSION_MODULES", "[]")))
    port = int(os.environ.get("CONSILITH_REFEREE_PORT", "8720"))
    # Server URLs are operator configuration only; a plugin's own ``servers`` never adds one.
    servers = json.loads(os.environ.get("CONSILITH_SOLVER_SERVERS", "{}"))
    token = os.environ.get("CONSILITH_REFEREE_TOKEN", "")
    if len(token) < 16:
        raise SystemExit(
            "CONSILITH_REFEREE_TOKEN (at least 16 characters, shared with the room) is required"
        )
    key = load_or_create_key(
        Path(os.environ.get("CONSILITH_REFEREE_KEY", "var/referee/ed25519.pem"))
    )
    uvicorn.run(
        create_app(
            key,
            f"http://127.0.0.1:{port}",
            servers=servers,
            token=token,
            check_ceiling_s=float(
                os.environ.get("CONSILITH_REFEREE_CEILING_S", DEFAULT_CHECK_CEILING_S)
            ),
        ),
        host="127.0.0.1",
        port=port,
    )
