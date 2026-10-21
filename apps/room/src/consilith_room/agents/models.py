"""The model port: the one place a model is called (stack rule 9).

The language model decides, labels and talks; it never produces a number (BRD design
principle). Every structured output schema registered here is checked to contain no numeric
fields, so a model cannot slip a value into the twin. Numbers that appear in a person's own
words (a reason such as "under 5 mm") are read by deterministic parsing, not by the model.

Providers: ``scripted`` (default, deterministic, used in every test and in CI), ``anthropic``,
``openai`` (Responses API), ``gemini``; ``siwc`` uses the person's ChatGPT plan through its
authenticated local Node helper. Keys belong to the signed-in session that put them
(platform/keys.py), live in memory only, and never enter room state, the journal or a log line.
"""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from ..domain.prefs import DEFAULT_MODELS

M = TypeVar("M", bound=BaseModel)
_OUTPUT_BUDGET: ContextVar[int] = ContextVar("model_output_budget", default=800)


class NotConfigured(RuntimeError):
    """The provider is declared but cannot run here (no key, or not built yet)."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


def assert_no_numbers(schema: type[BaseModel]) -> None:
    def walk(node: Any, where: str) -> None:
        if isinstance(node, dict):
            t = node.get("type")
            types = t if isinstance(t, list) else [t]
            if "number" in types or "integer" in types:
                raise TypeError(
                    f"{schema.__name__}{where} is numeric: the model never produces numbers"
                )
            for k, v in node.items():
                walk(v, f"{where}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{where}[{i}]")

    walk(schema.model_json_schema(), "")


class ModelPort(Protocol):
    name: str

    async def reply(self, system: str, text: str) -> str: ...

    async def structured(self, system: str, text: str, schema: type[M]) -> M: ...


@dataclass
class ScriptedModel:
    """Deterministic rules. It labels and phrases; it has no numbers of its own."""

    name: str = "scripted"
    script: dict[str, str] = field(default_factory=dict[str, str])
    # Said after a reply when this stands in for a model the person chose but has no key for.
    note: str = ""

    async def reply(self, system: str, text: str) -> str:
        for needle, answer in self.script.items():
            if needle.lower() in text.lower():
                return answer
        return f"Noted: {text.strip()}"

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


class _HTTPModel:
    """Shared shape for hosted providers. Built only when the owner has a key."""

    name = "hosted"

    def __init__(self, key: str, model: str) -> None:
        if not key:
            raise NotConfigured(f"{self.name}: no key for this agent's owner")
        self._key = key
        self.model = model

    def __repr__(self) -> str:
        return f"{type(self).__name__}(model={self.model!r}, key=[redacted])"

    async def reply(self, system: str, text: str) -> str:
        raise NotImplementedError

    async def structured(self, system: str, text: str, schema: type[M]) -> M:
        assert_no_numbers(schema)
        budget = _OUTPUT_BUDGET.set(8192)
        try:
            raw = await self.reply(
                system
                + "\nAnswer with one JSON object matching this schema, nothing else:\n"
                + json.dumps(schema.model_json_schema()),
                text,
            )
        finally:
            _OUTPUT_BUDGET.reset(budget)
        start, end = raw.find("{"), raw.rfind("}")
        return schema.model_validate_json(raw[start : end + 1])


class AnthropicModel(_HTTPModel):
    name = "anthropic"

    async def reply(self, system: str, text: str) -> str:
        try:
            import anthropic  # pyright: ignore[reportMissingImports]
        except ImportError as exc:
            raise NotConfigured("the anthropic package is not installed") from exc
        client: Any = anthropic.AsyncAnthropic(api_key=self._key)  # pyright: ignore[reportUnknownMemberType]
        msg: Any = await client.messages.create(
            model=self.model,
            max_tokens=_OUTPUT_BUDGET.get(),
            system=system,
            messages=[{"role": "user", "content": text}],
        )
        return "".join(getattr(b, "text", "") for b in msg.content)


class OpenAIModel(_HTTPModel):
    name = "openai"

    async def reply(self, system: str, text: str) -> str:
        try:
            import openai  # pyright: ignore[reportMissingImports]
        except ImportError as exc:
            raise NotConfigured("the openai package is not installed") from exc
        client: Any = openai.AsyncOpenAI(api_key=self._key)  # pyright: ignore[reportUnknownMemberType]
        resp: Any = await client.responses.create(
            model=self.model,
            instructions=system,
            input=text,
            max_output_tokens=_OUTPUT_BUDGET.get(),
        )
        return str(resp.output_text)


class GeminiModel(_HTTPModel):
    name = "gemini"

    async def reply(self, system: str, text: str) -> str:
        try:
            from google import genai  # pyright: ignore[reportMissingImports, reportAttributeAccessIssue]
        except ImportError as exc:
            raise NotConfigured("the google-genai package is not installed") from exc
        client: Any = genai.Client(api_key=self._key)  # pyright: ignore[reportUnknownMemberType]
        resp: Any = await client.aio.models.generate_content(
            model=self.model,
            contents=text,
            config={"system_instruction": system, "max_output_tokens": _OUTPUT_BUDGET.get()},
        )
        return str(resp.text)


class SiwcClient:
    """The local Sign in with ChatGPT sidecar (apps/siwc, docs/siwc.md). It holds the ChatGPT
    session on this machine; the room only sends prompts and gets text back."""

    def __init__(self, url: str, secret: str, owner_file: Any = None) -> None:
        self.url = url.rstrip("/")
        self._secret = secret
        # The helper holds one ChatGPT sign-in for this machine. It belongs to the person who
        # connected it: only they see it, sign it out, or run their agent on it.
        self._owner_file = owner_file
        self.owner: str | None = None
        if owner_file is not None and owner_file.exists():
            self.owner = owner_file.read_text(encoding="utf-8").strip() or None

    def set_owner(self, person: str | None) -> None:
        self.owner = person
        if self._owner_file is not None:
            if person:
                self._owner_file.write_text(person, encoding="utf-8")
            elif self._owner_file.exists():
                self._owner_file.unlink()

    @property
    def available(self) -> bool:
        return bool(self._secret)

    def __repr__(self) -> str:
        return f"SiwcClient(url={self.url!r}, secret=[redacted])"

    async def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        import httpx

        if not self._secret:
            raise NotConfigured("Sign in with ChatGPT is not set up on this server")
        try:
            # The DevKit bounds response streaming at180s; allow its safe error to return.
            async with httpx.AsyncClient(timeout=190 if path == "/reply" else 60) as client:
                resp = await client.request(
                    method,
                    self.url + path,
                    json=body,
                    headers={"X-Consilith-Siwc": self._secret},
                )
        except httpx.HTTPError as exc:
            raise NotConfigured(
                f"the Sign in with ChatGPT helper is not running ({type(exc).__name__})",
                code="helper_unavailable",
            ) from None
        try:
            data: Any = resp.json() if resp.content else {}
        except ValueError:
            raise NotConfigured(
                "The ChatGPT helper returned an invalid response.", code="helper_invalid_response"
            ) from None
        if not isinstance(data, dict):
            raise NotConfigured(
                "The ChatGPT helper returned an invalid response.", code="helper_invalid_response"
            )
        if resp.status_code >= 400:
            err = data.get("error", {})
            code = err.get("code") if isinstance(err, dict) else None
            safe_errors = {
                "subscription_sharing_usage_limit_exceeded": (
                    "A ChatGPT sharing limit has been reached. "
                    "Check your usage and app limits in ChatGPT Settings."
                ),
                "not_signed_in": "Sign in with ChatGPT again to continue.",
                "plan_usage_not_enabled": "Enable ChatGPT plan usage in the sign-in consent flow.",
                "busy": "The ChatGPT connection is busy. Wait for the current operation to finish.",
                "network_error": "The ChatGPT provider could not be reached.",
                "cancelled": "The ChatGPT request was cancelled.",
                "forbidden": "The ChatGPT helper connection is not authorized.",
            }
            # Only fixed, known messages leave this boundary. Never reflect an arbitrary
            # upstream body, even if a provider happens to include prompts or credentials.
            if isinstance(code, str) and code in safe_errors:
                raise NotConfigured(safe_errors[code], code=code)
            raise NotConfigured("The ChatGPT provider refused the request.", code="provider_error")
        return data


# Set by the runtime from settings; None until then (tests may set their own).
SIWC: SiwcClient | None = None


class SignInWithChatGPT:
    """The person's own ChatGPT plan, through the local sidecar. No API key."""

    name = "siwc"

    def __init__(self, client: SiwcClient | None, model: str | None = None) -> None:
        if client is None or not client.available:
            raise NotConfigured("Sign in with ChatGPT is not set up on this server")
        self._client = client
        self.model = model

    async def reply(self, system: str, text: str) -> str:
        body: dict[str, Any] = {"system": system, "text": text}
        if self.model:
            body["model"] = self.model
        out = await self._client.call("POST", "/reply", body)
        return str(out.get("text", ""))

    async def structured(self, system: str, text: str, schema: type[M]) -> M:
        assert_no_numbers(schema)
        raw = await self.reply(
            system
            + "\nAnswer with one JSON object matching this schema, nothing else:\n"
            + json.dumps(schema.model_json_schema()),
            text,
        )
        start, end = raw.find("{"), raw.rfind("}")
        return schema.model_validate_json(raw[start : end + 1])


# Only defaults we know are current; other providers need an explicit model in the drawer.


NO_KEY_NOTE = (
    "No model is connected for this session, so this answer is scripted. "
    "Add a key under Your agent to use {provider}."
)


# Chat models only: OpenAI lists embeddings, audio, image and moderation models too.
_OPENAI_CHAT = re.compile(r"^(gpt-|o\d|chatgpt-)")
_OPENAI_NOT_CHAT = re.compile(
    r"(audio|realtime|transcribe|tts|image|embedding|search|moderation|dall-e|whisper|instruct)"
)


async def list_models(provider: str, key: str) -> list[dict[str, str]]:
    """The models this key can use, newest first, as ``[{id, label}]``. The provider is asked
    directly, so new models appear without a release of ours. The key is never in an error."""
    try:
        if provider == "anthropic":
            import anthropic

            client: Any = anthropic.AsyncAnthropic(api_key=key)
            items = [m async for m in client.models.list(limit=100)]
            items.sort(key=lambda m: str(getattr(m, "created_at", "")), reverse=True)
            return [{"id": m.id, "label": getattr(m, "display_name", None) or m.id} for m in items]
        if provider == "openai":
            import openai

            oclient: Any = openai.AsyncOpenAI(api_key=key)
            models = [m async for m in oclient.models.list()]
            chat = [
                m for m in models if _OPENAI_CHAT.match(m.id) and not _OPENAI_NOT_CHAT.search(m.id)
            ]
            chat.sort(key=lambda m: int(getattr(m, "created", 0) or 0), reverse=True)
            return [{"id": m.id, "label": m.id} for m in chat]
        if provider == "siwc":
            if SIWC is None:
                raise NotConfigured("Sign in with ChatGPT is not set up on this server")
            listed = await SIWC.call("GET", "/models")
            return [
                {"id": str(m["id"]), "label": str(m.get("label") or m["id"])}
                for m in listed.get("models", [])
            ]
        if provider == "gemini":
            from google import genai  # pyright: ignore[reportMissingImports, reportAttributeAccessIssue]

            gclient: Any = genai.Client(api_key=key)
            out: list[dict[str, str]] = []
            async for m in await gclient.aio.models.list():
                actions = getattr(m, "supported_actions", None) or []
                name = str(getattr(m, "name", "") or "")
                if "generateContent" not in actions or not name:
                    continue
                mid = name.removeprefix("models/")
                out.append({"id": mid, "label": getattr(m, "display_name", None) or mid})
            out.sort(key=lambda m: m["id"], reverse=True)
            return out
    except Exception as exc:
        # The SDKs put request details in their messages; only the kind and status go out.
        status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        raise NotConfigured(
            f"{provider} refused the model list ({type(exc).__name__}"
            + (f", {status}" if status else "")
            + "): check the key"
        ) from None
    raise NotConfigured(f"{provider} has no model list")


def model_for(provider: str, key: str | None, model: str | None = None) -> ModelPort:
    """The model an agent uses. Without a key it is the scripted model, and it says so."""
    if provider == "scripted":
        return ScriptedModel()
    if provider == "siwc":
        return SignInWithChatGPT(SIWC, model)
    if not key:
        return ScriptedModel(note=NO_KEY_NOTE.format(provider=provider))
    cls = {"anthropic": AnthropicModel, "openai": OpenAIModel, "gemini": GeminiModel}[provider]
    chosen = model or DEFAULT_MODELS.get(provider)
    if not chosen:
        raise NotConfigured(f"{provider}: set model_name in the agent drawer")
    return cls(key, chosen)
