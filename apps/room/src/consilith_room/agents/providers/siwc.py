"""Sign in with ChatGPT: the person's own ChatGPT plan, through the local sidecar.

The bridge takes instructions plus one input string and returns text and the model it ran
(``S/app.ts``). It has no tools, no schema, no output cap, no usage and no stop reason, so this
adapter reports ``stop="end"`` (or ``"empty"``), no usage, and records the bridge's ``model``. The
model is pinned: it is sent on every call, never chosen by the bridge.
"""

from __future__ import annotations

from typing import Any

from ...platform.errors import ModelError, NotConfigured
from ...platform.siwc_client import SiwcClient
from ..model_types import Completion, Request
from . import register
from .base import (
    PortShims,
    complete_with_policy,
    make_error,
    text_of,
)

# The bridge's safe codes (platform/siwc_client.py) -> the shared error family. The words stay the
# bridge's own fixed text; only the code and the action are ours.
_BRIDGE_CODES: dict[str, tuple[str, str | None]] = {
    "subscription_sharing_usage_limit_exceeded": ("quota", "check_plan"),
    "plan_usage_not_enabled": ("not_configured", "reconnect"),
    "not_signed_in": ("not_configured", "reconnect"),
    "forbidden": ("not_configured", "reconnect"),
    "helper_unavailable": ("network", "retry"),
    "network_error": ("network", "retry"),
    "busy": ("overloaded", "retry"),
    "cancelled": ("cancelled", None),
    "provider_error": ("model_error", "retry"),
    "helper_invalid_response": ("model_error", "retry"),
}


def from_bridge(exc: NotConfigured) -> ModelError:
    """Map a bridge failure to a ``ModelError`` with the shared code and the bridge's own safe
    words. An unknown code is a generic failure with the generic words."""
    mapped = _BRIDGE_CODES.get(exc.code)
    if mapped is None:
        return exc
    code, action = mapped
    return make_error(code, message=exc.user_message, fix_action=action)


@register("siwc")
class SignInWithChatGPT(PortShims):
    """The person's own ChatGPT plan, through the local sidecar. No API key."""

    name = "siwc"
    provider = "siwc"

    def __init__(self, client: SiwcClient | None, model: str | None = None) -> None:
        if client is None or not client.available:
            raise NotConfigured("Sign in with ChatGPT is not set up on this server")
        self._client = client
        self.model = model or ""

    async def _attempt(self, req: Request) -> Completion:
        if not self.model:
            raise NotConfigured(
                "no ChatGPT model is chosen",
                user_message="Choose a ChatGPT model in Your agent.",
                fix_action="choose_model",
            )
        body: dict[str, Any] = {"system": req.instructions, "text": req.input, "model": self.model}
        try:
            out: Any = await self._client.call("POST", "/reply", body)
        except NotConfigured as exc:
            raise from_bridge(exc) from None
        data: dict[str, Any] = out if isinstance(out, dict) else {}  # pyright: ignore[reportUnknownVariableType]
        text = text_of(data.get("text"))
        used = text_of(data.get("model")) or self.model
        return Completion(text=text, stop="end" if text.strip() else "empty", model_used=used)

    async def complete(self, req: Request) -> Completion:
        return await complete_with_policy(lambda: self._attempt(req), req, provider="siwc")


__all__ = ["SignInWithChatGPT", "from_bridge"]
