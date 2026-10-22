"""A small instruction set built from one run's own tables.

Deterministic and pure: the same ``RunTables`` give the same rows in the same order. Every
number in a completion is a table value printed through ``sig``, or a change between two of
them printed through ``sig`` and ``percent``, so it matches the report. The rows are
illustrative training text about a hypothetical model, not data about a material.
"""

import hashlib
import json
from collections.abc import Callable, Iterator
from itertools import zip_longest

from .run_tables import DISCLAIMER, RunTables, percent, sig

MIN_ROWS = 40
MAX_ROWS = 200
COLUMNS = ["prompt", "completion"]
NOTE = " " + DISCLAIMER

Row = tuple[str, str]


def _movement(first: float, last: float) -> str:
    """How a quantity moved; the sentence always names both end values."""
    if first == last:
        return f"did not change from {sig(first)} to {sig(last)}"
    word = "rose" if last > first else "fell"
    if not first:
        return f"{word} from {sig(first)} to {sig(last)}"
    share = percent(100 * (last - first) / first)
    return f"{word} from {sig(first)} to {sig(last)}, a change of {share}% of its starting value"


def _pairs(frames: int) -> Iterator[tuple[int, int]]:
    """Every ordered pair of states, nearest first, so a capped list stays varied."""
    for gap in range(1, frames):
        for i in range(frames - gap):
            yield i, i + gap


def _change_rows(t: RunTables, quantity: str, unit: str, read: Callable[[int], float]) -> list[Row]:
    rows: list[Row] = []
    for i, j in _pairs(t.frames):
        start, end = f"t={sig(t.times[i])}", f"t={sig(t.times[j])}"
        rows.append(
            (
                f"How did the {quantity} change between {start} and {end}?",
                f"Between {start} and {end} (dimensionless time) the {quantity} "
                f"{_movement(read(i), read(j))} ({unit})." + NOTE,
            )
        )
    return rows


def _value_rows(t: RunTables, label: str, unit: str, read: Callable[[int], float]) -> list[Row]:
    return [
        (
            f"What was the {label} at t={sig(t.times[i])}?",
            f"At t={sig(t.times[i])} (dimensionless time) the {label} was "
            f"{sig(read(i))} ({unit})." + NOTE,
        )
        for i in range(t.frames)
    ]


def _grain_rows(t: RunTables) -> list[Row]:
    """Grain area at a state, ordered so the first rows already span grains and states."""
    grains, frames = len(t.grain_ids), t.frames
    rows: list[Row] = []
    for s in range(grains * frames):
        i = s % frames
        g = (s // frames + i) % grains
        gid = t.grain_ids[g]
        rows.append(
            (
                f"What was the diffuse area of grain {gid} at t={sig(t.times[i])}?",
                f"At t={sig(t.times[i])} (dimensionless time) grain {gid} had a diffuse area of "
                f"{sig(t.areas[i][g])} (px squared)." + NOTE,
            )
        )
    return rows


def _summary_rows(t: RunTables) -> list[Row]:
    last = t.frames - 1
    fast, slow = t.fastest_shrinking(), t.slowest_shrinking()
    span = f"t={sig(t.times[0])} to t={sig(t.times[last])}"
    gone = t.vanished()
    rows: list[Row] = [
        (
            "Which grain shrank fastest?",
            f"Grain {t.grain_ids[fast]} shrank fastest: its diffuse area fell from "
            f"{sig(t.areas[0][fast])} to {sig(t.areas[last][fast])} (px squared) between {span}, "
            f"a loss of {percent(100 * t.fractional_loss(fast))}% of its first area." + NOTE,
        ),
        (
            "Which grain shrank least?",
            f"Grain {t.grain_ids[slow]} shrank least: its diffuse area went from "
            f"{sig(t.areas[0][slow])} to {sig(t.areas[last][slow])} (px squared) between {span}, "
            f"a loss of {percent(100 * t.fractional_loss(slow))}% of its first area." + NOTE,
        ),
        (
            "How many grains were visible at the start and at the end of the run?",
            f"{t.counts[0]} grains were visible at t={sig(t.times[0])} and {t.counts[last]} at "
            f"t={sig(t.times[last])} (dimensionless time)." + NOTE,
        ),
        (
            "Which grains disappeared during the run?",
            (
                f"Grains {', '.join(str(g) for g in gone)} had no diffuse area left at "
                f"t={sig(t.times[last])} (dimensionless time)."
                if gone
                else f"No grain had lost all of its diffuse area by t={sig(t.times[last])} "
                "(dimensionless time)."
            )
            + NOTE,
        ),
        (
            "How long was the evolution?",
            f"The recorded states span t={sig(t.times[0])} to t={sig(t.times[last])} "
            f"(dimensionless time), in {t.frames} computed states." + NOTE,
        ),
    ]
    for key, label in (("gamma", "interface energy"), ("mobility", "boundary mobility")):
        if key in t.parameters:
            rows.append(
                (
                    f"What {label} did this run use?",
                    f"The run used a {label} of {sig(t.parameters[key])} (dimensionless)." + NOTE,
                )
            )
    return rows


def build_rows(t: RunTables) -> list[dict[str, str]]:
    """40 to 200 prompt/completion rows from the run, or ``ValueError`` if it is too small."""
    categories = [
        _summary_rows(t),
        _value_rows(t, "free energy", "dimensionless", lambda i: t.energy[i]),
        _value_rows(t, "number of visible grains", "grains", lambda i: float(t.counts[i])),
        _value_rows(t, "mean grain area", "px squared", t.mean_area),
        _change_rows(t, "mean grain area", "px squared", t.mean_area),
        _change_rows(t, "free energy", "dimensionless", lambda i: t.energy[i]),
        _change_rows(t, "number of visible grains", "grains", lambda i: float(t.counts[i])),
        _grain_rows(t),
    ]
    rows: list[Row] = []
    seen: set[str] = set()
    # Round-robin across categories keeps a capped set varied; a prompt is never repeated.
    for group in zip_longest(*categories):
        for item in group:
            if item is not None and item[0] not in seen:
                seen.add(item[0])
                rows.append(item)
        if len(rows) >= MAX_ROWS:
            break
    rows = rows[:MAX_ROWS]
    if len(rows) < MIN_ROWS:
        raise ValueError(
            f"this run yields only {len(rows)} distinct rows; at least {MIN_ROWS} are needed. "
            "Evolve with more recorded states or grains."
        )
    return [{"prompt": p, "completion": c} for p, c in rows]


def to_jsonl(rows: list[dict[str, str]]) -> bytes:
    """One JSON object per line, sorted keys, UTF-8: byte-stable for the same rows."""
    lines = [json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":")) for r in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def summary(rows: list[dict[str, str]], payload: bytes) -> dict:
    return {
        "rows": len(rows),
        "columns": list(COLUMNS),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "sizeBytes": len(payload),
        "disclaimer": DISCLAIMER,
        "source": "Derived deterministically from the run's own tables; illustrative text.",
    }
