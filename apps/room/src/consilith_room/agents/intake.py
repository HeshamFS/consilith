"""The conductor routes intake by each studio's input grammar (FR-LOOP-11).

An intake is a kind (``sketch``, ``micrograph``, ``formula``...) and the person's words. Inside a
studio's grammar it is routed there. Outside every grammar the conductor says what it cannot
simulate, offers the closest supported case and asks; it never guesses a case and runs it.
"""

from __future__ import annotations

import re
from typing import Any

from consilith_contracts import CaseDef
from consilith_contracts.extensions import all_extensions


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9.\-]+", text.lower()))


def closest(
    text: str, kind: str | None = None, cases: dict[str, CaseDef] | None = None
) -> str | None:
    words = _words(text) | ({kind.lower()} if kind else set())
    scores = {
        cid: sum(1 for h in hints if h in words)
        for cid, hints in all_extensions("intake_hints").items()
        if cases is None or cid in cases
    }
    best = max(scores.values(), default=0)
    return None if best == 0 else max(scores, key=lambda c: scores[c])


def route(
    kind: str | None, text: str = "", cases: dict[str, CaseDef] | None = None
) -> dict[str, Any]:
    """Where an intake goes. ``supported`` is False when it is outside every grammar."""
    cases = all_extensions("cases") if cases is None else cases
    lowered = text.lower()
    kinds = {kind: cid for cid, descriptor in cases.items() for kind in descriptor.inputs}
    kinds.update({k: cid for k, cid in all_extensions("intake_kinds").items() if cid in cases})
    case = kinds.get(kind or "")
    target = case or closest(text, kind, cases)
    for pattern, cannot, question in all_extensions("intake_outside").get(target or "", []):
        if re.search(pattern, lowered):
            return _outside(target, cannot, question, cases)
    if case is not None:
        return {
            "supported": True,
            "case": case,
            "label": cases[case].label,
            "grammar": cases[case].grammar,
        }
    if target is None:
        return {
            "supported": False,
            "cannot": f"no studio reads {kind or 'this input'}",
            "closest": None,
            "studios": {cid: c.grammar for cid, c in cases.items()},
            "question": "Which of the studios is closest to what you want to know?",
        }
    accepted = ", ".join(cases[target].inputs)
    return _outside(
        target,
        f"{kind or 'this input'} is not an input the studios read (accepted: {accepted})",
        f"Can you give it as {accepted} for the {cases[target].label} studio?",
        cases,
    )


def _outside(
    case: str | None, cannot: str, question: str, cases: dict[str, CaseDef]
) -> dict[str, Any]:
    out: dict[str, Any] = {"supported": False, "cannot": cannot, "question": question}
    out["closest"] = case
    if case is not None:
        out["label"] = cases[case].label
        out["grammar"] = cases[case].grammar
    return out


__all__ = ["closest", "route"]
