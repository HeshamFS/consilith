"""Model keys: in memory, per person and session, never anywhere else.

A key is put by a signed-in session and belongs to that session alone. It expires with the
session, is dropped at sign-out and is gone when the server restarts. A run started from a
person's own session uses that session's key first; every other run (background work, a peer
turn, an event turn, or another person's agent resolved inside this session) uses the newest live
session of the agent's owner that holds one. A session is never asked for a key on behalf of a
different person, so resolving person B inside person A's session leaves A's keys alone. With no
key the agent has no model and says so.

Nothing here is written to the store, the journal, a log line or an error message.
"""

from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from .store import Store

_current_sid: ContextVar[str | None] = ContextVar("consilith_session", default=None)


@contextmanager
def started_from(sid: str | None) -> Generator[None]:
    """Mark work as started by one session, so its model calls use that session's key."""
    token = _current_sid.set(sid)
    try:
        yield
    finally:
        _current_sid.reset(token)


def current_session() -> str | None:
    return _current_sid.get()


class SessionKeys:
    def __init__(self, store: Store) -> None:
        self._store = store
        self._keys: dict[str, dict[str, str]] = {}  # sid -> provider -> key
        self._owner: dict[str, str] = {}  # sid -> person
        self._lock = threading.Lock()

    def put(self, person: str, sid: str, provider: str, key: str) -> None:
        self._purge_dead()
        with self._lock:
            self._keys.setdefault(sid, {})[provider] = key
            self._owner[sid] = person

    def _purge_dead(self) -> None:
        """Forget the keys of sessions that ended or expired without a sign-out."""
        with self._lock:
            owners = dict(self._owner)
        for sid, person in owners.items():
            if not self._store.session_live(sid, person):
                self.drop_session(sid)

    def drop_provider(self, sid: str, provider: str | None = None) -> None:
        with self._lock:
            if provider is None:
                self._keys.pop(sid, None)
                self._owner.pop(sid, None)
            else:
                self._keys.get(sid, {}).pop(provider, None)

    def drop_session(self, sid: str) -> None:
        self.drop_provider(sid)

    def has(self, person: str, sid: str, provider: str) -> bool:
        return self.for_session(person, sid, provider) is not None

    def for_session(self, person: str, sid: str, provider: str) -> str | None:
        """The key this session put, while the session is live. A session that holds keys for
        someone else is not touched: it answers None and keeps its keys."""
        with self._lock:
            holder = self._owner.get(sid)
        if holder is not None and holder != person:
            return None
        if not self._store.session_live(sid, person):
            self.drop_session(sid)  # expired or revoked: the key goes with it
            return None
        with self._lock:
            return self._keys.get(sid, {}).get(provider)

    def for_background(self, person: str, provider: str) -> str | None:
        """The key of the newest live session of this person that holds one."""
        for sid in self._store.live_sessions(person):
            with self._lock:
                key = self._keys.get(sid, {}).get(provider)
            if key is not None:
                return key
        return None

    def has_live_session(self, person: str) -> bool:
        """Whether this person is signed in somewhere right now."""
        return bool(self._store.live_sessions(person))

    def ready(self, person: str, provider: str) -> bool:
        """The one readiness rule for a key provider: some live session of this person holds a
        key for it. Chat, the composer and peer turns all ask this."""
        return self.for_background(person, provider) is not None

    def for_run(self, person: str, provider: str) -> str | None:
        """The key a run uses for ``person``'s agent.

        The run's own session is used first, but only when it is this person's. Anything else
        (background work, or a run started from another person's session) uses the newest live
        session of ``person`` that holds a key. ``for_session`` is never called with another
        person's session, so a cross-person turn cannot drop the caller's keys.
        """
        sid = current_session()
        if sid is not None:
            with self._lock:
                holder = self._owner.get(sid)
            if holder == person:
                own = self.for_session(person, sid, provider)
                if own is not None:
                    return own
        return self.for_background(person, provider)

    def __repr__(self) -> str:
        with self._lock:
            count = sum(len(v) for v in self._keys.values())
        return f"SessionKeys({count} keys, redacted)"
