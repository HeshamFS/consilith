"""A2A 1.0 hosting for the room's agents (FR-PR-6).

Every hosted agent of every room is served under ``/a2a/{room}/{agent}/``:
- ``.well-known/agent-card.json``: its card, rebuilt from room state on every fetch;
- ``jsonrpc``: its JSON-RPC binding (``SendMessage``, ``GetTask``...).

Inbound calls are authenticated with room-bound agent tokens; a call without one reaches the
executor as an anonymous caller, which every Consilith executor refuses.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from a2a.auth.user import User
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import AgentCard, CancelTaskRequest, Task
from a2a.utils.errors import TaskNotCancelableError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from ..platform.tokens import TokenError, TokenIssuer

PREFIX = "/a2a/"
ROOM_SYSTEM = "room"


class RoomRequestHandler(DefaultRequestHandler):
    """Only the room server may cancel a hosted task. The check runs before the SDK touches
    the task, because the SDK marks a task cancelled or failed once its executor is asked."""

    async def on_cancel_task(
        self, params: CancelTaskRequest, context: ServerCallContext
    ) -> Task | None:
        if (context.state or {}).get("caller") != ROOM_SYSTEM:
            raise TaskNotCancelableError(message="only the room may cancel this task")
        return await super().on_cancel_task(params, context)


class AgentUser(User):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._name


class TokenContextBuilder(DefaultServerCallContextBuilder):
    """The SDK's default context (headers, extensions) plus the caller id from a room-bound
    agent token. The bearer token itself is dropped from the stored headers."""

    def __init__(self, tokens: TokenIssuer, room: str) -> None:
        self.tokens = tokens
        self.room = room

    def build(self, request: Request) -> ServerCallContext:
        ctx = super().build(request)
        caller: str | None = None
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            try:
                claims = self.tokens.verify(auth.removeprefix("Bearer ").strip(), kind="agent")
                if claims.room == self.room:
                    caller = claims.sub
            except TokenError:
                caller = None
        headers = {
            k: v for k, v in ctx.state.get("headers", {}).items() if k.lower() != "authorization"
        }
        ctx.state.update({"headers": headers, "caller": caller, "room": self.room})
        if caller:
            ctx.user = AgentUser(caller)
        return ctx


def room_scope(ctx: ServerCallContext) -> str:
    """Tasks are visible across the room's authenticated agents (the conductor's proposal tasks
    are continued by the room itself); anonymous callers see only an empty scope. Who may
    change a task is decided by the executor, not by the store."""
    caller = ctx.state.get("caller")
    return f"room:{ctx.state.get('room')}" if caller else "anonymous"


def caller_of(context: Any) -> str | None:
    call = getattr(context, "call_context", None)
    state = getattr(call, "state", None) or {}
    return state.get("caller")


@dataclass
class HostedAgent:
    card: Callable[[], AgentCard]
    executor: AgentExecutor


Resolver = Callable[[str, str], HostedAgent | None]


class A2ADispatch:
    """ASGI middleware: routes ``/a2a/{room}/{agent}/...`` to that agent's own sub-app."""

    def __init__(self, app: ASGIApp, resolve: Resolver, tokens: TokenIssuer) -> None:
        self.app = app
        self.resolve = resolve
        self.tokens = tokens
        self._apps: dict[tuple[str, str], Starlette] = {}

    def _sub_app(self, room: str, aid: str) -> Starlette | None:
        key = (room, aid)
        if key in self._apps:
            return self._apps[key]
        hosted = self.resolve(room, aid)
        if hosted is None:
            return None

        async def fresh_card(_card: AgentCard) -> AgentCard:
            return hosted.card()

        first = hosted.card()
        handler = RoomRequestHandler(
            agent_executor=hosted.executor,
            task_store=InMemoryTaskStore(owner_resolver=room_scope),
            agent_card=first,
        )
        routes = [
            *create_agent_card_routes(agent_card=first, card_modifier=fresh_card),
            *create_jsonrpc_routes(
                request_handler=handler,
                rpc_url="/jsonrpc",
                context_builder=TokenContextBuilder(self.tokens, room),
            ),
        ]
        sub = Starlette(routes=routes)
        self._apps[key] = sub
        return sub

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path: str = scope.get("path", "")
        if scope["type"] == "http" and path.startswith(PREFIX):
            parts = path[len(PREFIX) :].split("/", 2)
            if len(parts) == 3:
                room, aid, rest = parts
                sub = self._sub_app(room, aid)
                if sub is not None:
                    child = dict(scope)
                    child["path"] = "/" + rest
                    child["raw_path"] = ("/" + rest).encode()
                    child["root_path"] = scope.get("root_path", "") + f"{PREFIX}{room}/{aid}"
                    await sub(child, receive, send)
                    return
            await _not_found(send)
            return
        await self.app(scope, receive, send)


async def _not_found(send: Send) -> None:
    body = b'{"error":{"code":"not_found","message":"no such agent"}}'
    await send(
        {
            "type": "http.response.start",
            "status": 404,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


CardFactory = Callable[[], Awaitable[AgentCard]]
