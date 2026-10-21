"""Outbound A2A 1.0 calls: agent to agent, room to referee, room to brought agents.

Calls to the room's own hosted agents use an in-process ASGI transport (same JSON-RPC wire,
no network). Every request and response is journaled with sender and receiver (NFR-9).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers.proto_helpers import get_data_parts, get_text_parts, new_data_part
from a2a.types import AgentCard, Message, Part, Role, SendMessageRequest, TaskState
from google.protobuf.json_format import MessageToDict

from .journal import Journal

CARD_PATH = "/.well-known/agent-card.json"


def _within_local_base(url: httpx.URL, base: httpx.URL) -> bool:
    prefix = base.path.rstrip("/")
    return (url.scheme, url.host, url.port) == (base.scheme, base.host, base.port) and (
        not prefix or url.path == prefix or url.path.startswith(prefix + "/")
    )


class _LocalTransport(httpx.AsyncBaseTransport):
    """Map the advertised reverse-proxy mount to the room's unmounted ASGI app."""

    def __init__(self, app: Any, base: str) -> None:
        self.base = httpx.URL(base)
        self.transport = httpx.ASGITransport(app=app)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not _within_local_base(request.url, self.base):
            raise ValueError("A2A local request is outside the configured public mount")
        path = request.url.path[len(self.base.path.rstrip("/")) :] or "/"
        internal = httpx.Request(
            request.method,
            request.url.copy_with(path=path),
            headers=request.headers,
            stream=request.stream,
            extensions=request.extensions,
        )
        return await self.transport.handle_async_request(internal)

    async def aclose(self) -> None:
        await self.transport.aclose()


@dataclass
class A2AReply:
    task_id: str | None = None
    context_id: str | None = None
    state: str | None = None
    texts: list[str] = field(default_factory=list[str])
    data: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    artifacts: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])


class A2ACaller:
    def __init__(
        self,
        journal: Journal,
        *,
        local_base: str,
        local_app: Callable[[], Any] | None = None,
        timeout_s: float = 120.0,
        client_factory: Callable[[str], httpx.AsyncClient] | None = None,
    ) -> None:
        self.journal = journal
        self.local_base = local_base.rstrip("/")
        self.local_app = local_app
        self.timeout_s = timeout_s
        self.client_factory = client_factory

    def _http(self, base: str, token: str | None) -> httpx.AsyncClient:
        headers = {"A2A-Version": "1.0"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if self.client_factory is not None:
            client = self.client_factory(base)
            client.headers.update(headers)
            return client
        if self.local_app is not None and _within_local_base(
            httpx.URL(base), httpx.URL(self.local_base)
        ):
            transport = _LocalTransport(self.local_app(), self.local_base)
            return httpx.AsyncClient(
                transport=transport,
                base_url=self.local_base,
                headers=headers,
                timeout=self.timeout_s,
            )
        return httpx.AsyncClient(headers=headers, timeout=self.timeout_s, follow_redirects=False)

    async def card(self, base: str) -> AgentCard:
        async with self._http(base, None) as http:
            return await A2ACardResolver(http, base, CARD_PATH).get_agent_card()

    async def send(
        self,
        room: str,
        *,
        sender: str,
        receiver: str,
        base: str,
        text: str | None = None,
        data: dict[str, Any] | None = None,
        media_type: str | None = None,
        token: str | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        card: AgentCard | None = None,
    ) -> A2AReply:
        parts: list[Part] = []
        if text:
            parts.append(Part(text=text))
        if data is not None:
            parts.append(new_data_part(data, media_type=media_type))
        msg = Message(
            role=Role.ROLE_USER,
            message_id=str(uuid.uuid4()),
            parts=parts,
            context_id=context_id or "",
            task_id=task_id or "",
        )
        corr = msg.message_id
        self.journal.record(
            room,
            "A2A",
            "SendMessage",
            sender,
            receiver,
            {"text": text, "data": data, "mediaType": media_type, "taskId": task_id},
            correlation_id=corr,
        )
        reply = A2AReply()
        async with self._http(base, token) as http:
            agent_card = card or await A2ACardResolver(http, base, CARD_PATH).get_agent_card()
            client = await create_client(
                agent_card,
                client_config=ClientConfig(
                    httpx_client=http, supported_protocol_bindings=["JSONRPC"], streaming=False
                ),
            )
            try:
                async for ev in client.send_message(SendMessageRequest(message=msg)):
                    _absorb(reply, ev)
            finally:
                await client.close()
        self.journal.record(
            room,
            "A2A",
            "SendMessage.result",
            receiver,
            sender,
            {
                "taskId": reply.task_id,
                "state": reply.state,
                "texts": reply.texts,
                "data": reply.data,
                "artifacts": reply.artifacts,
            },
            correlation_id=corr,
        )
        return reply


def _absorb(reply: A2AReply, ev: Any) -> None:
    if ev.HasField("task"):
        task = ev.task
        reply.task_id, reply.context_id = task.id, task.context_id
        reply.state = TaskState.Name(task.status.state)
        if task.status.HasField("message"):
            reply.texts += get_text_parts(list(task.status.message.parts))
            reply.data += get_data_parts(list(task.status.message.parts))
        for art in task.artifacts:
            reply.artifacts.append(MessageToDict(art))
            reply.data += get_data_parts(list(art.parts))
    elif ev.HasField("status_update"):
        reply.state = TaskState.Name(ev.status_update.status.state)
    elif ev.HasField("artifact_update"):
        art = ev.artifact_update.artifact
        reply.artifacts.append(MessageToDict(art))
        reply.data += get_data_parts(list(art.parts))
    elif ev.HasField("message"):
        reply.texts += get_text_parts(list(ev.message.parts))
        reply.data += get_data_parts(list(ev.message.parts))
