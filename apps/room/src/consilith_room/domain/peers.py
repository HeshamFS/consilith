"""Agent-to-agent threads in the room aggregate: ``post_peer``, the thread index and ``caused_by``.

The room document carries a small ``/peers`` node, created on first use the way ``followups`` is,
so an existing room needs no migration:

- ``threads``: the index of at most 8 open threads (``PeerThreadIndex``: ids, counters, states);
- ``prefs``: per person, whether colleagues' agents may ask their agent, a pause switch, the
  senders they muted, the people their agent may write to, and the lesson opt-in (decision 20);
- ``causes``: proposal id to the peer message that caused it (shown on the approval card);
- ``room``: the room-level switch for external (brought) recipients.

Message bodies never pass through here: ``post_peer`` writes the body to F3's ``peer_messages``
and commits a log row that holds ids only (``{thread, msgId, from, to, kind, hop, length}``).
"""

from __future__ import annotations

import time
from typing import Any, cast

from consilith_contracts import (
    PEER_MAX_HOPS,
    PEER_MAX_OPEN_THREADS,
    PEER_MAX_THREAD_MESSAGES,
    PeerNotice,
    PeerPending,
    PeerThreadIndex,
    new_id,
)

from ..platform.stores.peer_messages import PeerMessageRow
from .actors import Actor
from .document import Patch
from .errors import Conflict, Denied, NotFound, RoomError
from .paths import pointer
from .room import RoomCore, locked

CLOSED_KEPT = 4  # closed threads the index keeps for the interface, besides the open ones
CAUSE_KEPT = 200


class PeerLimit(RoomError):
    """A thread limit was reached (hops, messages per thread, open threads): the conductor
    shows a calm "capped" notice and nothing more is sent."""

    code = "peer_capped"


def default_prefs() -> dict[str, Any]:
    # ``confirmSends``: the owner sees a card before their agent writes to another agent. Off by
    # default (Hesham, 2026-10-22): asking the agent in chat is the approval.
    return {
        "accepts": False,
        "paused": False,
        "lessons": False,
        "confirmSends": False,
        "muted": [],
        "contacts": [],
    }


class PeerRoom(RoomCore):
    """The part of the room aggregate that agent-to-agent threads need."""

    # reads -----------------------------------------------------------------------------------
    def peer_threads(self) -> dict[str, PeerThreadIndex]:
        raw = cast(dict[str, Any], self.read("peers", "threads") or {})
        return {tid: PeerThreadIndex.model_validate(node) for tid, node in raw.items()}

    def peer_thread(self, thread: str) -> PeerThreadIndex:
        node = self.read("peers", "threads", thread)
        if node is None:
            raise NotFound(f"no thread {thread}")
        return PeerThreadIndex.model_validate(node)

    def peer_prefs(self, person: str) -> dict[str, Any]:
        """A person's peer settings with the defaults filled in: opt-in is off."""
        return {
            **default_prefs(),
            **cast(dict[str, Any], self.read("peers", "prefs", person) or {}),
        }

    def peer_external_allowed(self) -> bool:
        return self.read("peers", "room", "external") is not False

    def peer_cause(self, proposal: str) -> dict[str, Any] | None:
        node = self.read("peers", "causes", proposal)
        return cast("dict[str, Any] | None", node)

    def peer_causes(self) -> dict[str, dict[str, Any]]:
        return cast(dict[str, dict[str, Any]], self.read("peers", "causes") or {})

    def open_peer_threads(self) -> list[PeerThreadIndex]:
        return [t for t in self.peer_threads().values() if t.status == "open"]

    def pair_thread(self, a: str, b: str) -> PeerThreadIndex | None:
        """The open thread between two people, if any: one open thread per pair."""
        for t in self.open_peer_threads():
            if set(t.participants) == {a, b}:
                return t
        return None

    # settings --------------------------------------------------------------------------------
    @locked
    def set_peer_prefs(self, person: str, **changes: Any) -> dict[str, Any]:
        """Change a person's own peer settings: ``accepts``, ``paused``, ``lessons``, ``muted``
        (the people whose agents cannot write to theirs), ``contacts`` (people their agent has
        been approved to write to). Unknown names are refused."""
        self._require_seat(person)
        allowed = set(default_prefs())
        bad = set(changes) - allowed
        if bad:
            raise RoomError(f"unknown peer setting: {', '.join(sorted(bad))}")
        for key in ("accepts", "paused", "lessons", "confirmSends"):
            if key in changes and not isinstance(changes[key], bool):
                raise RoomError(f"{key} is on or off")
        for key in ("muted", "contacts"):
            if key in changes:
                raw: object = changes[key]
                items = cast("list[object]", raw) if isinstance(raw, list) else None
                if items is None or not all(isinstance(x, str) for x in items):
                    raise RoomError(f"{key} is a list of people")
                changes[key] = sorted({str(x) for x in items})
        now = {**self.peer_prefs(person), **changes}
        ops = self._peers_root()
        ops.append({"op": "add", "path": pointer("peers", "prefs", person), "value": now})
        self._commit(
            Actor.person(person), "peer.prefs", ops, {"person": person, "set": sorted(changes)}
        )
        return now

    @locked
    def set_peer_external(self, person: str, allowed: bool) -> None:
        """The room-level switch that forbids messaging external (brought) agents."""
        self._require_seat(person)
        ops = self._peers_root()
        ops.append({"op": "add", "path": pointer("peers", "room"), "value": {"external": allowed}})
        self._commit(Actor.person(person), "peer.prefs", ops, {"room": True, "external": allowed})

    @locked
    def allow_peer_contact(self, person: str, other: str) -> None:
        """The owner approved first contact: their agent may write to ``other``'s agent."""
        prefs = self.peer_prefs(person)
        if other in prefs["contacts"]:
            return
        self.set_peer_prefs(person, contacts=[*prefs["contacts"], other])

    # the index -------------------------------------------------------------------------------
    def _peers_root(self) -> Patch:
        if self.read("peers") is not None:
            return []
        return [
            {
                "op": "add",
                "path": "/peers",
                "value": {"threads": {}, "prefs": {}, "causes": {}, "room": {}},
            }
        ]

    def _index_ops(self, idx: PeerThreadIndex, *, fresh: bool) -> Patch:
        wire = idx.wire()
        base = pointer("peers", "threads", idx.id)
        if fresh:
            return [{"op": "add", "path": base, "value": wire}]
        ops: Patch = []
        for key in ("count", "lastTs", "status", "hop"):
            ops.append({"op": "add", "path": f"{base}/{key}", "value": wire[key]})
        for key in ("pending", "notice"):
            ops.append({"op": "add", "path": f"{base}/{key}", "value": wire.get(key)})
        return ops

    def _trim_ops(self, keep: str) -> Patch:
        """Drop the oldest closed threads past ``CLOSED_KEPT`` when a thread is created."""
        closed = sorted(
            (t for t in self.peer_threads().values() if t.status == "closed" and t.id != keep),
            key=lambda t: t.last_ts,
        )
        extra = closed[: max(0, len(closed) - CLOSED_KEPT)]
        return [{"op": "remove", "path": pointer("peers", "threads", t.id)} for t in extra]

    # post_peer -------------------------------------------------------------------------------
    @locked
    def post_peer(
        self,
        *,
        msg_id: str,
        sender: str,
        recipient: str,
        sender_agent: str,
        recipient_agent: str,
        kind: str,
        body: str,
        hop: int,
        thread: str | None = None,
        in_reply_to: str | None = None,
        refs: list[dict[str, Any]] | None = None,
        deliver: bool = True,
        notice: PeerNotice | None = None,
        now: float | None = None,
    ) -> tuple[PeerThreadIndex, bool]:
        """Commit one peer message: the body to ``peer_messages``, ids only to the log, and the
        thread index to the room document, in that order. Returns the index and whether the
        message is new (a retry of the same ``msg_id`` stores nothing a second time).

        ``deliver`` marks the recipient's agent as replying (the interface's indicator); it is
        false for a message that ends the exchange (the hop cap), which carries ``notice``.
        Raises ``PeerLimit`` past 4 hops, 12 messages in a thread, or 8 open threads."""
        self._require_seat(sender)
        self._require_seat(recipient)
        if sender == recipient:
            raise Denied("an agent does not write to its own owner's agent")
        ts = time.time() if now is None else now
        prior = self.existing_op(Actor.agent_of(sender), msg_id)
        if prior is not None:
            return self.peer_thread(str(prior.meta["thread"])), False
        idx = self.peer_thread(thread) if thread else None
        fresh = idx is None
        if idx is not None:
            if idx.status != "open":
                raise Conflict("that thread is closed")
            if set(idx.participants) != {sender, recipient}:
                raise Denied("that thread is between two other people")
            if idx.count >= PEER_MAX_THREAD_MESSAGES:
                raise PeerLimit(
                    f"this thread reached {PEER_MAX_THREAD_MESSAGES} messages; a person must "
                    "continue in a new one"
                )
        else:
            if len(self.open_peer_threads()) >= PEER_MAX_OPEN_THREADS:
                raise PeerLimit(
                    f"this room already has {PEER_MAX_OPEN_THREADS} open threads; close one first"
                )
        if hop > PEER_MAX_HOPS:
            raise PeerLimit(f"agents may pass a message on {PEER_MAX_HOPS} times at most")
        tid = idx.id if idx is not None else new_id("thr")
        row = PeerMessageRow(
            room=self.id,
            thread=tid,
            msg_id=msg_id,
            from_person=sender,
            to_person=recipient,
            from_agent=sender_agent,
            to_agent=recipient_agent,
            kind=kind,
            hop=hop,
            in_reply_to=in_reply_to,
            body=body,
            meta={"refs": refs or []},
            ts=ts,
        )
        updated = PeerThreadIndex(
            id=tid,
            participants=idx.participants if idx else [sender, recipient],
            agents=idx.agents if idx else [sender_agent, recipient_agent],
            opener=idx.opener if idx else sender,
            count=(idx.count if idx else 0) + 1,
            last_ts=ts,
            status="open",
            hop=hop,
            pending=PeerPending(agent=recipient_agent, msg_id=msg_id) if deliver else None,
            notice=notice,
        )
        ops: Patch = self._peers_root()
        if fresh:
            ops += self._trim_ops(tid)
        ops += self._index_ops(updated, fresh=fresh)
        meta = {
            "thread": tid,
            "msgId": msg_id,
            "from": sender_agent,
            "to": recipient_agent,
            "kind": kind,
            "hop": hop,
            "length": len(body),
        }
        # The log row (ids only) first, then the body: a body with no index would show in the
        # thread list, while an index whose body failed to store is only a short thread.
        _, fresh_row = self._commit(Actor.agent_of(sender), "peer.message", ops, meta, msg_id)
        if fresh_row:
            self.store.peer_messages.insert(row)
        return updated, fresh_row

    @locked
    def set_peer_state(
        self,
        thread: str,
        *,
        pending: PeerPending | None = None,
        notice: PeerNotice | None = None,
        clear_notice: bool = False,
    ) -> PeerThreadIndex:
        """Change what the thread shows: who is replying, and a calm notice. A pending reply
        and a notice replace the previous ones; ``clear_notice`` removes the notice."""
        idx = self.peer_thread(thread)
        updated = idx.model_copy(
            update={
                "pending": pending,
                "notice": None if clear_notice else (notice or idx.notice),
            }
        )
        if updated == idx:
            return idx
        ops = self._index_ops(updated, fresh=False)
        meta: dict[str, Any] = {"thread": thread, "pending": pending.agent if pending else None}
        if notice is not None:
            meta["notice"] = notice.code
        self._commit(Actor.backend(), "peer.thread", ops, meta)
        return updated

    @locked
    def close_peer_thread(
        self, thread: str, by: str | None, notice: PeerNotice | None = None
    ) -> PeerThreadIndex:
        """Close a thread. Either participant may; the server does so when a participant leaves.
        The bodies stay for the retention period, except when ``by`` is None (a person left)."""
        idx = self.peer_thread(thread)
        if by is not None and by not in idx.participants:
            raise Denied("only the two people in a thread close it")
        if idx.status == "closed":
            return idx
        shown = notice or PeerNotice(code="closed", text="This thread was closed.")
        updated = idx.model_copy(
            update={"status": "closed", "pending": None, "notice": shown, "last_ts": time.time()}
        )
        actor = Actor.person(by) if by else Actor.backend()
        self._commit(
            actor, "peer.thread.closed", self._index_ops(updated, fresh=False), {"thread": thread}
        )
        return updated

    # caused_by -------------------------------------------------------------------------------
    @locked
    def record_peer_cause(
        self, proposal: str, msg_id: str, sender_agent: str, thread: str, now: float | None = None
    ) -> None:
        """Remember which peer message caused a proposal (``caused_by``). The Proposal record
        itself is unchanged: the approval card reads this node."""
        self.proposal_exists(proposal)
        causes = self.peer_causes()
        ops = self._peers_root()
        if len(causes) >= CAUSE_KEPT:  # the oldest causes of decided proposals go first
            oldest = sorted(causes, key=lambda k: float(causes[k].get("ts", 0)))[:1]
            ops += [{"op": "remove", "path": pointer("peers", "causes", k)} for k in oldest]
        ops.append(
            {
                "op": "add",
                "path": pointer("peers", "causes", proposal),
                "value": {
                    "msg": msg_id,
                    "from": sender_agent,
                    "thread": thread,
                    "ts": time.time() if now is None else now,
                },
            }
        )
        self._commit(
            Actor.backend(),
            "peer.cause",
            ops,
            {"proposal": proposal, "msgId": msg_id, "from": sender_agent},
        )

    def proposal_exists(self, pid: str) -> None:
        if self.read("proposals", pid) is None:
            raise NotFound(f"no proposal {pid}")

    def peer_lesson_allowed(self, prop_id: str, holder_owner: str) -> bool:
        """Whether deciding this proposal may teach its author a lesson. A proposal a peer
        message caused teaches none unless the author's owner opted in (decision 20)."""
        if self.peer_cause(prop_id) is None:
            return True
        return bool(self.peer_prefs(holder_owner)["lessons"])
