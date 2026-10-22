"""The JSON-action envelope: how every provider calls tools (section 3).

A model answers with exactly one envelope: ``{"do":"call","calls":[{"tool","args"}]}`` (at most
four calls) or ``{"do":"say","text"}``. A reply counts as a call only when the whole reply, after
stripping one code fence, is exactly one envelope object. Anything else is a ``say``: text around
an object, an object quoted inside prose and a forged envelope inside a tool result are all
prose, which is safe because prose is guarded and authority stays in ToolHost.

An object that names ``"do":"call"`` or ``"do":"say"`` but does not validate (an unknown field, a
fifth call, a number in the arguments, an unknown tool) is repaired: the error goes back to the
model, up to two times, then ``BadOutputError``. Over-long ``say`` text gets one repair that asks
for a shorter answer, then a host-side cut.

This module has no knowledge of tools, rooms or providers. ``next_step`` calls any ``ModelPortV2``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from consilith_contracts.agent_tools import (
    CallEnvelope,
    EnvelopeCall,
    SayEnvelope,
    assert_model_safe_schema,
)
from pydantic import ValidationError

from ...platform.errors import BadOutputError
from ..model_types import Completion, ModelPortV2, Request
from .base import (
    CURRENT_PRINCIPAL,
    DEFAULT_DEADLINE_S,
    VISIBLE_TEXT_CAP,
    cap_visible_text,
    check_stop,
    error_message,
)

# The grammar, as the prompt builder puts it into the instructions. It names no provider.
ENVELOPE_INSTRUCTIONS = (
    "Answer with exactly one JSON object and nothing else.\n"
    'To use tools: {"do":"call","calls":[{"tool":"<tool name>","args":{...}}]} with at most 4 '
    "calls. Arguments hold strings, booleans, lists and objects only, never a number: name a "
    "number's source with a binding.\n"
    'To answer the person: {"do":"say","text":"<your answer>"}.\n'
    "Anything that is not exactly one such object is shown to the person as your answer."
)

MAX_REPAIRS = 2

# One check per call: the problem as text, or None when the call is fine. The tool host supplies
# it (known alias, arguments against the tool's schema).
CallCheck = Callable[[EnvelopeCall], str | None]

_FENCE = re.compile(r"\A```[A-Za-z0-9_-]*[ \t]*\n?(.*?)\n?```\Z", re.DOTALL)
_DECODER = json.JSONDecoder()
# A reply that opens like an envelope. If it then fails to parse it is a broken envelope to
# repair, not prose: showing half a JSON object to a person helps nobody.
_ENVELOPE_OPENING = re.compile(r'\A\{\s*"do"\s*:\s*"(?:call|say)"')


def assert_envelope_safe() -> None:
    """The envelope schemas must pass the model-safe schema guard (no numbers can be typed)."""
    assert_model_safe_schema(CallEnvelope.model_json_schema())
    assert_model_safe_schema(SayEnvelope.model_json_schema())


@dataclass(frozen=True)
class Say:
    """Prose for the person."""

    text: str
    lenient: bool = False  # True when the reply was not an envelope and is shown as it came


@dataclass(frozen=True)
class Calls:
    """Validated calls, in order. At most four."""

    calls: tuple[EnvelopeCall, ...]


@dataclass(frozen=True)
class Rejected:
    """An object that claims to be an envelope but does not validate."""

    problem: str


Parsed = Say | Calls | Rejected


def parse_reply(raw: str) -> Parsed:
    """Read one reply. Never raises."""
    body = raw.strip()
    fenced = _FENCE.match(body)
    if fenced is not None:
        body = fenced.group(1).strip()
    if not body.startswith("{"):
        return Say(raw.strip(), lenient=True)
    try:
        value, end = _DECODER.raw_decode(body)
    except ValueError:
        if _ENVELOPE_OPENING.match(body):
            return Rejected("the reply is not one complete, valid JSON object")
        return Say(raw.strip(), lenient=True)
    if body[end:].strip() or not isinstance(value, dict):
        return Say(raw.strip(), lenient=True)  # more than one object, or text around it
    kind = value.get("do")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    try:
        if kind == "call":
            return Calls(tuple(CallEnvelope.model_validate(value).calls))
        if kind == "say":
            return Say(SayEnvelope.model_validate(value).text)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'reply'}: {e['msg']}"
            for e in exc.errors(include_url=False, include_input=False)
        )
        return Rejected(problems[:600])
    return Say(raw.strip(), lenient=True)


@dataclass
class Step:
    """What one model step produced, with the facts to record for it."""

    kind: Literal["say", "call"]
    text: str = ""
    calls: tuple[EnvelopeCall, ...] = ()
    lenient: bool = False
    repairs: int = 0
    completions: list[Completion] = field(default_factory=list[Completion])

    @property
    def model_used(self) -> str:
        return self.completions[-1].model_used if self.completions else ""


async def next_step(
    port: ModelPortV2,
    *,
    instructions: str,
    input: str,
    max_output: int,
    trace_id: str,
    principal: str | None = None,
    deadline_s: float = DEFAULT_DEADLINE_S,
    check: CallCheck | None = None,
    text_cap: int = VISIBLE_TEXT_CAP,
    repairs: int = MAX_REPAIRS,
) -> Step:
    """One model step: ask, read the envelope, repair, and return a ``Say`` or validated calls.

    ``BadOutputError`` after ``repairs`` failed repairs. ``TruncatedError`` at once for a
    ``length`` stop: a cut-off envelope cannot close, and its calls are discarded. A refusal, an
    empty reply and the other stop rules are raised by the port itself.
    """
    who = principal if principal is not None else CURRENT_PRINCIPAL.get()
    feedback = ""
    used = 0
    shortened = False
    done: list[Completion] = []
    while True:
        reply = await port.complete(
            Request(
                instructions=instructions,
                input=input + feedback,
                max_output=max_output,
                deadline_s=deadline_s,
                principal=who,
                trace_id=trace_id,
            )
        )
        done.append(reply)
        check_stop(reply, envelope=True)
        parsed = parse_reply(reply.text)
        problem: str | None = None
        if isinstance(parsed, Rejected):
            problem = parsed.problem
        elif isinstance(parsed, Calls) and check is not None:
            for call in parsed.calls:
                problem = check(call)
                if problem is not None:
                    problem = f"{call.tool}: {problem}"
                    break
        elif isinstance(parsed, Say) and len(parsed.text) > text_cap and not shortened:
            shortened = True
            feedback = (
                f"\n\nYour previous answer was longer than {text_cap} characters. "
                "Answer again, shorter, as one envelope."
            )
            continue
        if problem is None:
            if isinstance(parsed, Calls):
                return Step("call", calls=parsed.calls, repairs=used, completions=done)
            assert isinstance(parsed, Say)
            return Step(
                "say",
                text=cap_visible_text(parsed.text, text_cap),
                lenient=parsed.lenient,
                repairs=used,
                completions=done,
            )
        if used >= repairs:
            raise BadOutputError(error_message("bad_output"), fix_action="retry")
        used += 1
        feedback = (
            "\n\nYour previous reply was rejected: "
            + problem
            + '\nReply again with exactly one envelope object: {"do":"call",...} or '
            '{"do":"say","text":...}.'
        )
