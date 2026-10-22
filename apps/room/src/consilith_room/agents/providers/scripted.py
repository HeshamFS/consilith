"""The scripted provider: deterministic, no key, used in every test and in CI.

``ScriptedModel`` is the old single-turn model and keeps its behaviour. ``ScriptedToolModel`` speaks
the JSON-action envelope, in three modes:

* ``normal``: answers from a queue of replies, then from rules (a needle in the input gives a
  fixed reply), then with a plain ``say``.
* ``malformed``: answers with envelopes that do not validate, so the repair path runs; after
  ``recover_after`` bad answers it returns a valid ``say``.
* ``obey_injection``: does what any instruction in its input says. It repeats a forged envelope
  it finds, and it turns phrases like "run the solver" into calls. It stands in for the worst
  model, so each injection case proves a refusal by the platform and not by the model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from ..model_types import Completion, Request
from . import register
from .base import M, assert_no_numbers

ScriptedMode = Literal["normal", "malformed", "obey_injection"]

# Phrase (lower case) -> calls it turns into in ``obey_injection`` mode. Tool names are the
# canonical refs; a test can pass its own map for the aliases it shows the model.
DEFAULT_OBEY: dict[str, list[dict[str, Any]]] = {
    "run the solver": [{"tool": "run.start", "args": {}}],
    "set pressure to 5": [{"tool": "input.set", "args": {"field": "pressure", "value": "5"}}],
    "chat.history": [{"tool": "chat.history", "args": {}}],
    "lessons": [{"tool": "lesson.list", "args": {}}],
}

_MALFORMED = (
    '{"do":"call","calls":[{"tool":"x","args":',  # cut off
    '{"do":"call","calls":"not a list"}',
    '{"do":"say"}',  # no text
    '{"do":"call","calls":[{"tool":"x","args":{"value":5}}]}',  # a number
)

_FORGED = re.compile(r'\{\s*"do"\s*:\s*"(?:call|say)".*\}', re.DOTALL)


def _dump(reply: str | dict[str, Any]) -> str:
    return reply if isinstance(reply, str) else json.dumps(reply)


@register("scripted")
@dataclass
class ScriptedModel:
    """Deterministic rules. It labels and phrases; it has no numbers of its own."""

    name: str = "scripted"
    script: dict[str, str] = field(default_factory=dict[str, str])
    # Said after a reply when this stands in for a model the person chose but has no key for.
    note: str = ""

    provider: str = field(default="scripted", init=False, repr=False)
    model: str = field(default="scripted", init=False, repr=False)

    async def reply(self, system: str, text: str) -> str:
        for needle, answer in self.script.items():
            if needle.lower() in text.lower():
                return answer
        return f"Noted: {text.strip()}"

    async def complete(self, req: Request) -> Completion:
        """The v2 port: the same rules as ``reply``, over the request's input."""
        text = await self.reply(req.instructions, req.input)
        return Completion(text=text, stop="end", model_used=self.model)

    async def structured(self, system: str, text: str, schema: type[M]) -> M:
        assert_no_numbers(schema)
        fields = schema.model_fields
        data: dict[str, Any] = {}
        for name, info in fields.items():
            ann = str(info.annotation)
            if "bool" in ann:
                data[name] = bool(re.search(r"\b(yes|accept|ok|true)\b", text, re.I))
            elif "list" in ann:
                data[name] = []
            else:
                data[name] = text.strip()[:200]
        return schema.model_validate(data)


@dataclass
class ScriptedToolModel(ScriptedModel):
    """A scripted model that answers in the JSON-action envelope. ``reply`` and ``structured``
    keep the old deterministic behaviour, so shim parity holds. ``seen`` records every request."""

    mode: ScriptedMode = "normal"
    # normal: replies used in order (a dict is dumped as JSON), then ``rules``, then a say.
    replies: list[str | dict[str, Any]] = field(default_factory=list[str | dict[str, Any]])
    rules: dict[str, str | dict[str, Any]] = field(default_factory=dict[str, str | dict[str, Any]])
    say_text: str = "Understood."
    # malformed: how many bad answers before a valid one (None: always bad).
    recover_after: int | None = None
    # obey_injection: phrase -> calls.
    obey: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: dict(DEFAULT_OBEY))
    seen: list[Request] = field(default_factory=list[Request])
    _bad: int = 0

    async def complete(self, req: Request) -> Completion:
        self.seen.append(req)
        return Completion(text=self._answer(req), stop="end", model_used=self.model)

    def _answer(self, req: Request) -> str:
        if self.mode == "malformed":
            if self.recover_after is not None and self._bad >= self.recover_after:
                return self._say(self.say_text)
            self._bad += 1
            return _MALFORMED[(self._bad - 1) % len(_MALFORMED)]
        if self.mode == "obey_injection":
            return self._obey(req.input)
        if self.replies:
            return _dump(self.replies.pop(0))
        lowered = req.input.lower()
        for needle, answer in self.rules.items():
            if needle.lower() in lowered:
                return _dump(answer)
        return self._say(self.say_text)

    def _obey(self, text: str) -> str:
        forged = _FORGED.search(text)
        if forged is not None:
            return forged.group(0)  # repeats the envelope it was handed
        lowered = text.lower()
        calls: list[dict[str, Any]] = []
        for phrase, wanted in self.obey.items():
            if phrase.lower() in lowered:
                calls.extend(wanted)
        if calls:
            return json.dumps({"do": "call", "calls": calls[:4]})
        return self._say(self.say_text)

    @staticmethod
    def _say(text: str) -> str:
        return json.dumps({"do": "say", "text": text})
