"""Your agent: the choice a person makes once and brings to every room (M9 "Your agent").

The provider key is not part of it: a key lives in memory for one session (platform/keys.py).

This module also holds the operator's model table (the ``models`` section of ``deployment.json``)
and the one function that checks a ``(provider, model)`` pair, so the ``/me/agent`` route and the
room drawer's ``set_agent_config`` apply the same rule. Domain code calls
``validate_agent_model`` directly.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from consilith_contracts import (
    PROVIDERS,
    AgentConfig,
    Autonomy,
    ProviderId,
    keyed_provider_ids,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import RoomError

# The autonomy a new seat starts with, for a person and for a role that sets none. Every default
# site reads this one name (through this module, so a patch of it moves them all). It was SUGGEST
# until the taint and injection tests were green; they are, so it is EDIT_MY_LANES (decision 1).
# It does not touch an explicit choice: ``AgentConfig.autonomy`` and a plugin role keep their own.
NEW_SEAT_AUTONOMY: Autonomy = Autonomy.EDIT_MY_LANES

# Model calls per person per hour (decision 17). A person may lower or raise their own cap in
# these prefs, up to the operator's ceiling.
HOURLY_CAP_DEFAULT = 60
HOURLY_CAP_CEILING = 240

# Compatibility names, derived from the provider registry (consilith_contracts.providers).
# ``Provider`` is a ``str`` checked against the registry; old stored values still validate.
Provider = ProviderId
KEYED_PROVIDERS: tuple[str, ...] = keyed_provider_ids()
DEFAULT_MODELS: dict[str, str] = {
    p.id: p.default_model for p in PROVIDERS.values() if p.default_model is not None
}


# --- the operator's model table -------------------------------------------------------------


class ModelRow(BaseModel):
    """One ``models`` entry of ``deployment.json``: what an operator knows about a model."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    provider: str = Field(min_length=1, max_length=32)
    model: str = Field(min_length=1, max_length=100)
    context: int = Field(gt=0)  # the window, in tokens
    max_output: int = Field(gt=0)  # the most output tokens a request may ask for
    reasoning_headroom: int = Field(default=0, ge=0)  # extra output tokens kept for reasoning
    verified: bool = False  # the parity suite passed for this model

    def output_budget(self, wanted: int) -> int:
        """The output tokens to ask the provider for: the visible answer plus the reasoning
        headroom, never more than the model allows."""
        return max(1, min(wanted + self.reasoning_headroom, self.max_output))


class ModelTable:
    """The rows of the table, by ``(provider, model)``. Empty means no operator list: every
    model is accepted and shown as unverified."""

    def __init__(self, rows: tuple[ModelRow, ...] = ()) -> None:
        self.rows = rows
        self._by_key = {(r.provider, r.model): r for r in rows}

    @classmethod
    def from_document(cls, document: Mapping[str, Any] | None) -> ModelTable:
        """Read the ``models`` section of a deployment document. ``ValueError`` names a bad row."""
        raw: Any = (document or {}).get("models") or []
        if not isinstance(raw, list):
            raise ValueError("deployment models must be a list")
        rows: list[ModelRow] = []
        for index, item in enumerate(raw):  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
            try:
                row = ModelRow.model_validate(item)
            except ValidationError as exc:
                raise ValueError(
                    f"deployment models[{index}] is invalid: {exc.error_count()} problem(s)"
                ) from None
            if row.provider not in PROVIDERS:
                raise ValueError(f"deployment models[{index}] names an unknown provider")
            rows.append(row)
        return cls(tuple(rows))

    @classmethod
    def load(cls, path: Path) -> ModelTable:
        return cls.from_document(json.loads(path.read_text(encoding="utf-8")))

    def get(self, provider: str, model: str | None) -> ModelRow | None:
        return self._by_key.get((provider, model or ""))

    def for_provider(self, provider: str) -> list[ModelRow]:
        return [r for r in self.rows if r.provider == provider]

    def providers_of(self, model: str) -> set[str]:
        return {r.provider for r in self.rows if r.model == model}

    def verified(self, provider: str, model: str | None) -> bool:
        row = self.get(provider, model)
        return row is not None and row.verified

    def clamp_output(self, provider: str, model: str | None, wanted: int) -> int:
        """The output budget for a request. A model not in the table keeps what was asked."""
        row = self.get(provider, model)
        return wanted if row is None else row.output_budget(wanted)


_current_table = ModelTable()


def model_table() -> ModelTable:
    return _current_table


def set_model_table(table: ModelTable) -> None:
    """Install the operator's table (startup, from ``deployment.json``)."""
    global _current_table
    _current_table = table


# A model id that names its maker. The family is a hint that catches "an Anthropic model id sent
# to OpenAI"; a model with no known prefix is accepted and shown as unverified.
_FAMILIES: tuple[tuple[re.Pattern[str], frozenset[str]], ...] = (
    (re.compile(r"^claude", re.I), frozenset({"anthropic"})),
    (re.compile(r"^gemini", re.I), frozenset({"gemini"})),
    (re.compile(r"^(gpt-|chatgpt-|o\d)", re.I), frozenset({"openai", "siwc"})),
)


def validate_agent_model(provider: str, model: str | None, table: ModelTable | None = None) -> None:
    """Check a ``(provider, model)`` pair. Raises ``RoomError`` (code ``model_invalid``) with the
    words to show. Used by ``PUT /me/agent`` and by the room drawer's ``set_agent_config``.

    * ``scripted`` takes no model; every other provider needs one named.
    * A model the table lists for a different provider only, or whose id names another maker
      (``claude-...`` on OpenAI), is refused.
    * Any other model is accepted. The table marks which ones are verified.
    """
    descriptor = PROVIDERS.get(provider)
    if descriptor is None:
        raise RoomError(f"unknown model provider {provider[:24]!r}", code="model_invalid")
    name = (model or "").strip()
    if not descriptor.needs_model:
        if name:
            raise RoomError(f"{descriptor.label} takes no model", code="model_invalid")
        return
    if not name:
        raise RoomError(f"name the {provider} model to use", code="model_required")
    listed = (table or _current_table).providers_of(name)
    if listed and provider not in listed:
        owner = PROVIDERS[sorted(listed)[0]].label
        raise RoomError(
            f"{name} is a {owner} model, not a {descriptor.label} model", code="model_invalid"
        )
    for pattern, makers in _FAMILIES:
        if pattern.match(name) and provider not in makers:
            owner = PROVIDERS[sorted(makers)[0]].label
            raise RoomError(
                f"{name} is a {owner} model, not a {descriptor.label} model", code="model_invalid"
            )


def effective_hourly_cap(requested: int | None, ceiling: int = HOURLY_CAP_CEILING) -> int:
    """A person's cap: what they asked for (or the default), never above the operator's ceiling."""
    return max(1, min(requested if requested is not None else HOURLY_CAP_DEFAULT, ceiling))


def hourly_cap_of(stored_prefs: Mapping[str, Any] | None) -> int | None:
    """The cap a person chose, read from their stored prefs (None: they chose none)."""
    if not stored_prefs:
        return None
    value = stored_prefs.get("hourlyCap")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _new_seat_autonomy() -> Autonomy:
    return NEW_SEAT_AUTONOMY


class AgentPrefs(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    kind: Literal["consilith", "brought"] = "consilith"
    provider: Provider = "scripted"
    model: str | None = Field(default=None, max_length=100)
    autonomy: Autonomy = Field(default_factory=_new_seat_autonomy)
    card_url: str | None = Field(default=None, alias="cardUrl", max_length=2048)
    # Model calls per hour for this person; None means the default. Limited by the ceiling.
    hourly_cap: int | None = Field(default=None, alias="hourlyCap", ge=1)

    def problems(self) -> str | None:
        """Why these prefs cannot be stored, or None."""
        if self.kind == "brought" and not self.card_url:
            return "a brought agent needs its Agent Card URL"
        if self.kind == "consilith" and self.card_url:
            return "only a brought agent has a card URL"
        if self.hourly_cap is not None and self.hourly_cap > HOURLY_CAP_CEILING:
            return f"the most model calls per hour is {HOURLY_CAP_CEILING}"
        if self.kind == "consilith":
            try:
                validate_agent_model(self.provider, self.model)
            except RoomError as exc:
                return exc.message
        return None

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=False)


def default_prefs(discipline: str) -> AgentPrefs:
    from .roles import default_config  # roles reads NEW_SEAT_AUTONOMY from this module

    return AgentPrefs(autonomy=default_config(discipline).autonomy)


def seat_config(base: AgentConfig, prefs: AgentPrefs | None) -> AgentConfig:
    """The drawer a new seat starts with: the role's defaults, then what the person chose."""
    if prefs is None:
        return base
    return base.model_copy(
        update={
            "autonomy": prefs.autonomy,
            "model_provider": prefs.provider,
            "model_name": prefs.model,
        }
    )
