"""The model port: the one place a model is called (stack rule 9).

The language model decides, labels and talks; it never produces a number (BRD design
principle). Every structured output schema registered here is checked to contain no numeric
fields, so a model cannot slip a value into the twin. Numbers that appear in a person's own
words (a reason such as "under 5 mm") are read by deterministic parsing, not by the model.

Providers: ``scripted`` (default, deterministic, used in every test and in CI), ``anthropic``,
``openai`` (Responses API), ``gemini``; ``siwc`` uses the person's ChatGPT plan through its
authenticated local Node helper. Keys belong to the signed-in session that put them
(platform/keys.py), live in memory only, and never enter room state, the journal or a log line.

This module is the shim layer. The port itself (``complete``, ``reply``, ``structured``, the retry
and timeout policy) lives in ``providers/base.py``; which model an agent runs on, and whether it
can run, is ``model_resolver.py``; the meter is ``meter.py``. Names are re-exported here so older
imports keep working. ``model_for`` is the old factory: it builds one adapter and, with no key,
a scripted stand-in with a note. New code asks ``ModelResolver.for_principal`` instead, which
answers ``Unavailable(not_configured, ...)`` and never echoes.
"""

from __future__ import annotations

from ..domain.prefs import DEFAULT_MODELS as DEFAULT_MODELS
from ..platform.errors import NotConfigured as NotConfigured  # re-exported: it moved to platform
from ..platform.siwc_client import SiwcClient as SiwcClient
from .model_types import Completion as Completion
from .model_types import ModelPortV2 as ModelPortV2
from .model_types import Request as Request
from .providers import adapter_for
from .providers.anthropic_api import AnthropicModel as AnthropicModel
from .providers.base import _OUTPUT_BUDGET as _OUTPUT_BUDGET
from .providers.base import M as M
from .providers.base import ModelPort as ModelPort
from .providers.base import _HTTPModel as _HTTPModel
from .providers.base import assert_no_numbers as assert_no_numbers
from .providers.base import structured_with_retry as structured_with_retry
from .providers.gemini_api import GeminiModel as GeminiModel
from .providers.listing import list_models as list_models
from .providers.openai_api import OpenAIModel as OpenAIModel
from .providers.scripted import ScriptedModel as ScriptedModel
from .providers.scripted import ScriptedToolModel as ScriptedToolModel
from .providers.siwc import SignInWithChatGPT as SignInWithChatGPT

# Set by the runtime from settings; None until then (tests may set their own).
SIWC: SiwcClient | None = None

NO_KEY_NOTE = (
    "No model is connected for this session, so this answer is scripted. "
    "Add a key under Your agent to use {provider}."
)


def model_for(provider: str, key: str | None, model: str | None = None) -> ModelPort:
    """The model an agent uses. Without a key it is the scripted model, and it says so."""
    if provider == "scripted":
        return adapter_for("scripted")()
    if provider == "siwc":
        return adapter_for("siwc")(SIWC, model)
    if not key:
        return ScriptedModel(note=NO_KEY_NOTE.format(provider=provider))
    cls = adapter_for(provider)
    chosen = model or DEFAULT_MODELS.get(provider)
    if not chosen:
        raise NotConfigured(
            f"{provider}: set model_name in the agent drawer", fix_action="choose_model"
        )
    return cls(key, chosen)
