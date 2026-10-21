"""Generated analysis over existing results (FR-LOOP-10, FR-RG-8, NFR-4).

1. The inputs and their revision snapshot are read together, under the room lock.
2. The program is checked against the analysis language, then evaluated in a spawned child
   process killed at 10 s (``consilith_contracts.analysis.run_isolated``).
3. A run is opened, but the value is NOT stored yet. The referee evaluates the same program
   independently, in its own child process, and signs.
4. Only a verified, signed entry lets the value into shared state (results and ledger together).
   Anything else fails the run: an unchecked value is never stored, streamed or shown.
"""

from __future__ import annotations

import asyncio
from typing import Any

from consilith_contracts import (
    LedgerEntry,
    Provenance,
    ResultRecord,
    ReviewItem,
    ReviewRequest,
    RunManifest,
    Tier,
    Validity,
    digest,
    new_id,
)
from consilith_contracts.analysis import AnalysisError, run_isolated
from consilith_contracts.extensions import package_for, registry_operation
from consilith_contracts.results import result_binding_digest as result_digest
from consilith_contracts.review import REVIEW_MEDIA_TYPE
from consilith_contracts.solver import inputs_hash

from ..domain.actors import Actor
from ..domain.errors import RoomError
from ..domain.plugin_access import replay_policy, require_extension, room_pins
from ..domain.service import RoomService
from .conductor import CONDUCTOR, VERSION, Conductor

TIMEOUT_S = 10.0


def evaluate(
    source: str, results: dict[str, tuple[float, str]], timeout_s: float = TIMEOUT_S
) -> tuple[float, str, list[str]]:
    value, used = run_isolated(source, results, timeout_s)
    return value.magnitude, value.unit, used


@registry_operation
async def analyse(
    conductor: Conductor, room: RoomService, requester: Actor, case: str, source: str, question: str
) -> dict[str, Any]:
    conductor.authorize(room, requester, case)
    require_extension(room, "analysis_engines", "default")
    engine_pin = package_for("analysis_engines", "default")
    engine_version = engine_pin.version if engine_pin else VERSION
    with room.lock:  # inputs and revisions read together
        current = {
            r.id: (float(r.value), r.unit)
            for r in room.current_results(case).values()
            if r.kind == "number" and isinstance(r.value, (int, float))
        }
        snap = room.revisions(case)
    try:
        value, unit, used = await asyncio.to_thread(evaluate, source, current)
    except AnalysisError as exc:
        raise RoomError(f"analysis refused: {exc}") from exc
    inputs = {rid: {"value": current[rid][0], "unit": current[rid][1]} for rid in used}
    arguments: dict[str, Any] = {"source": source, "inputs": inputs, "question": question}
    manifest = RunManifest(
        tool="analysis.run",
        server="room",
        package_pins=room_pins(room),
        replay_policy=replay_policy(room, case, "analysis.run"),
        arguments=arguments,
        spec_revision=digest(snap),
        input_revisions=snap,
        solver_version=engine_version,
    )
    mdigest = digest(manifest)
    conductor.store.put_manifest(mdigest, manifest.wire())
    dep = Actor.room_agent("conductor", initiator=requester.person_id)
    rid = room.open_run(CONDUCTOR, case, "analysis.run", 0, mdigest, dep, input_revisions=snap)
    name = f"analysis_{digest({'source': source})[:8]}"
    res_id = new_id("res")
    item = ReviewItem(
        result_id=res_id,
        quantity=name,
        value=value,
        unit=unit,
        result_digest=result_digest(name, value, unit, rid, mdigest),
        input_revisions=snap,
        manifest_digest=mdigest,
        tier=Tier.HAND_CALC,
    )
    req = ReviewRequest(room=room.id, case=case, run_id=rid, manifest=manifest, items=[item])
    reply = await conductor.caller.send(
        room.id,
        sender="conductor",
        receiver="referee",
        base=conductor.referee_base,
        data=req.wire(),
        media_type=REVIEW_MEDIA_TYPE,
        token=conductor.referee_token,
    )
    entry = next(
        (
            LedgerEntry.model_validate(raw)
            for block in reply.data
            for raw in block.get("entries", [])
        ),
        None,
    )
    verified = (
        entry is not None and conductor.verify(entry) and entry.subject.stamp.value == "verified"
    )
    base: dict[str, Any] = {"source": source, "question": question, "used": used, "run": rid}
    if not verified or entry is None:
        room.fail_run(CONDUCTOR, rid, "failed", "the referee did not verify the analysis", [])
        return {
            **base,
            "shown": False,
            "stamp": entry.subject.stamp.value if entry else "checking",
            "value": None,
            "unit": None,
            "result": None,
            "ledger": [],
        }
    rec = ResultRecord(
        id=res_id,
        room=room.id,
        case=case,
        run_id=rid,
        quantity=name,
        value=value,
        unit=unit,
        tier=Tier.HAND_CALC,
        provenance=Provenance(
            tool="analysis.run",
            solver_version=engine_version,
            inputs_hash=inputs_hash(arguments),
            wall_time_s=0.0,
        ),
        validity=Validity(range="Consilith analysis language over stored results", inside=True),
        manifest_digest=mdigest,
        input_revisions={},
        result_digest="",
    )
    # Stored and stamped in one commit: the value is never in state unchecked.
    (stored,) = room.record_results(CONDUCTOR, rid, [rec], verified=(entry, conductor.verify))
    final = room.result(stored.id)
    shown = final.stamp.value == "verified"
    return {
        **base,
        "shown": shown,
        "stamp": final.stamp.value,
        "value": value if shown else None,
        "unit": unit if shown else None,
        "result": stored.id,
        "ledger": [entry.id],
    }
