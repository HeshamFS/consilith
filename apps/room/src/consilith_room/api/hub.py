"""Process-wide wiring: store, bus, journal, tokens, accounts and the live rooms."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any

from consilith_contracts import units
from consilith_contracts.extensions import (
    configure_extension_loader,
    default_case,
    load_extensions,
    plugin_descriptors,
)
from consilith_contracts.manifest_meta import check_ids_have_no_slash
from consilith_plugin_runtime import load as load_plugin
from consilith_plugin_runtime.catalog import read_catalog

from ..agents import cursor
from ..agents.runtime import Runtime
from ..agents.session import AfterDecision, Responder
from ..agents.turn_events import TurnRunner
from ..domain.accounts import Accounts
from ..domain.errors import Conflict, NotFound, RoomError
from ..domain.invites import Invites
from ..domain.plugin_access import server_owners
from ..domain.prefs import ModelTable, set_model_table
from ..domain.service import RoomService
from ..platform.bus import RoomBus
from ..platform.keys import SessionKeys
from ..platform.mail import Mailer, build_mailer
from ..platform.settings import Settings
from ..platform.store import Store
from ..platform.tokens import TokenIssuer
from ..protocols.github import GitHubClient, HttpGitHub
from ..protocols.journal import Journal

logger = logging.getLogger("consilith.hub")


class AgentLoopUnavailable(RoomError):
    """The agent loop is on, but no turn runner has been installed yet."""

    status = 503
    code = "agent_loop_unavailable"


def _load_model_table(settings: Settings) -> None:
    """Install the operator's model table from the ``models`` section of the deployment manifest.
    A named file that is missing or malformed stops the start; with none named, a
    ``deployment.json`` in the working directory is read (not under test)."""
    path = settings.deployment_json
    if path is None:
        guess = Path("deployment.json")
        if settings.env == "test" or not guess.is_file():
            return
        path = guess
    set_model_table(ModelTable.load(path))
    logger.info("model table loaded from %s", path.name)


async def _default_reply(room: RoomService, person: str, text: str) -> str:
    return f"{person}'s agent heard: {text}"


class Hub:
    def __init__(self, settings: Settings, store: Store | None = None) -> None:
        configure_extension_loader(load_plugin)
        catalog = read_catalog(settings.plugin_catalog) if settings.plugin_catalog else None
        if catalog is not None:
            os.environ["CONSILITH_PLUGIN_TOKENS"] = json.dumps(catalog["tokens"])
            load_extensions(catalog["references"])
            settings.solver_urls = catalog["solver_urls"]
        else:
            load_extensions(settings.extension_modules)
        self._catalog_revision = catalog["revision"] if catalog else None
        self._catalog_lock = threading.Lock()
        self.settings = settings
        self.store = store or Store(settings.data_dir / "consilith.sqlite")
        self.bus = RoomBus()
        self.journal = Journal(self.store, self.bus)
        self.tokens = TokenIssuer(settings.token_secret, settings.token_ttl_s)
        self.accounts = Accounts(self.store)
        self.invites = Invites(self.store, settings.invite_ttl_s)
        self.keys = SessionKeys(self.store)
        _load_model_table(settings)
        self.mailer: Mailer = build_mailer(settings)  # refuses to start in production without one
        # A new secret at every start; the app's start path writes it to var/dev.env.
        self.dev_capability: str | None = (
            secrets.token_urlsafe(32) if settings.dev_mailbox else None
        )
        self.github: GitHubClient | None = (
            HttpGitHub(
                settings.github_client_id,
                settings.github_client_secret.get_secret_value(),
                settings.github_callback_url,
            )
            if settings.github_enabled
            else None
        )
        self._rooms: dict[str, RoomService] = {}
        self._lock = threading.Lock()
        self.seen_messages: dict[str, set[str]] = defaultdict(set)
        self.respond: Responder = _default_reply
        self.asgi_app: Any = None
        self.runtime = Runtime(
            settings,
            self.store,
            self.journal,
            self.tokens,
            self.room,
            lambda: self.asgi_app,
            self._reply,
            self.keys,
        )
        self.runtime.registry.owners = server_owners(plugin_descriptors())
        # The one meter and the one resolver live on the runtime; the hub shows them.
        self.meter = self.runtime.meter
        self.resolver = self.runtime.resolver
        self._rediscovery: asyncio.Future[Any] | None = None
        self.after_decision: AfterDecision | None = self.runtime.after_decision
        self.respond = self.runtime.kernel.respond
        self.presence: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        # Agent cursors (agents/cursor.py): kept and published like a person's presence.
        cursor.attach(self.store, self._publish_cursor)
        self.extras: dict[str, Any] = {}
        units.registry()  # warm pint once, so no request pays for it

    def refresh_plugins(self) -> None:
        """Read an operator-owned catalog; clients cannot supply paths or install code.

        A changed catalog reloads the plugins, replaces the server list and rediscovers their
        tools, so a queued job after a catalog change finds the new tools."""
        if self.settings.plugin_catalog is None:
            return
        with self._catalog_lock:
            catalog = read_catalog(self.settings.plugin_catalog)
            if catalog["revision"] == self._catalog_revision:
                return
            os.environ["CONSILITH_PLUGIN_TOKENS"] = json.dumps(catalog["tokens"])
            load_extensions(catalog["references"])
            descriptors = plugin_descriptors()
            for manifest in descriptors:
                check_ids_have_no_slash(manifest)
            self.settings.solver_urls = catalog["solver_urls"]
            registry = self.runtime.registry
            registry.servers = dict(catalog["solver_urls"])
            registry.owners = server_owners(descriptors)
            registry.bump_revision()
            self._catalog_revision = catalog["revision"]
            self._rediscover()

    def _rediscover(self) -> None:
        """Run ``tools/list`` again on the current servers. On the event loop it is scheduled;
        from a worker thread (a sync route) it runs to completion before returning."""
        registry = self.runtime.registry
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                asyncio.run(registry.discover())
            except Exception:
                logger.exception("tool rediscovery after a catalog change failed")
            return
        task = asyncio.ensure_future(registry.discover())
        self._rediscovery = task
        task.add_done_callback(self._rediscovered)

    def _rediscovered(self, task: asyncio.Future[Any]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("tool rediscovery after a catalog change failed")

    async def _reply(self, room: RoomService, person: str, text: str) -> str:
        return await self.respond(room, person, text)

    def responder(self, loop_on: bool) -> Responder:
        """What answers a person's message in one piece: today's ``respond``. With the loop on
        the agent session uses ``turn_runner`` instead, so this stays the loop-off answer."""
        if loop_on:
            self.turn_runner(True)
        return self.respond

    def turn_runner(self, loop_on: bool) -> TurnRunner | None:
        """The runner the agent session consumes events from. Loop off: None, and nothing about
        the session changes. Loop on: the runner from ``runtime``, or a 503
        ``agent_loop_unavailable`` while none is set."""
        if not loop_on:
            return None
        runner = self.runtime.turn_runner
        if runner is None:
            raise AgentLoopUnavailable("the agent loop is on but has no turn runner")
        return runner

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.bus.bind_loop(loop)

    def _on_commit(self, room_id: str):
        def notify(_row: object) -> None:
            self.bus.notify(room_id)

        return notify

    def create_room(self, room_id: str, title: str, studio: str = "") -> RoomService:
        self.refresh_plugins()
        studio = studio or default_case()
        with self._lock:
            if room_id in {r for r, _ in self.store.rooms()}:
                raise Conflict(f"room id {room_id!r} is taken")
            room = RoomService.create(
                self.store, room_id, title, self._on_commit(room_id), studio=studio
            )
            self._rooms[room_id] = room
            return room

    def forget_room(self, room_id: str) -> None:
        """Drop a deleted room from memory and wake its feeds, which then find it gone."""
        with self._lock:
            self._rooms.pop(room_id, None)
        self.bus.notify(room_id)

    def _publish_cursor(self, room_id: str, event: dict[str, Any]) -> None:
        """An agent's cursor: the latest one is kept, and the room's live feeds get it."""
        self.presence[room_id][str(event["person"])] = event
        self.bus.publish_ephemeral(room_id, event)

    def room(self, room_id: str) -> RoomService:
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None:
                if room_id not in {r for r, _ in self.store.rooms()}:
                    raise NotFound(f"no room {room_id}")
                room = RoomService.load(self.store, room_id, self._on_commit(room_id))
                self._rooms[room_id] = room
            return room
