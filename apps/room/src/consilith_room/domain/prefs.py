"""Your agent: the choice a person makes once and brings to every room (M9 "Your agent").

The provider key is not part of it: a key lives in memory for one session (platform/keys.py).
"""

from __future__ import annotations

from typing import Any, Literal

from consilith_contracts import AgentConfig, Autonomy
from pydantic import BaseModel, ConfigDict, Field

from .roles import default_config

Provider = Literal["anthropic", "openai", "gemini", "scripted", "siwc"]
KEYED_PROVIDERS = ("anthropic", "openai", "gemini")
DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5"}


class AgentPrefs(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    kind: Literal["consilith", "brought"] = "consilith"
    provider: Provider = "scripted"
    model: str | None = Field(default=None, max_length=100)
    autonomy: Autonomy = Autonomy.SUGGEST
    card_url: str | None = Field(default=None, alias="cardUrl", max_length=2048)

    def problems(self) -> str | None:
        """Why these prefs cannot be stored, or None."""
        if self.kind == "brought" and not self.card_url:
            return "a brought agent needs its Agent Card URL"
        if self.kind == "consilith" and self.card_url:
            return "only a brought agent has a card URL"
        if self.kind == "consilith" and self.provider in ("openai", "gemini") and not self.model:
            return f"name the {self.provider} model to use"
        return None

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


def default_prefs(discipline: str) -> AgentPrefs:
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
