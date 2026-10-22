"""The one place the room server reads its environment (stack rule 4)."""

from __future__ import annotations

import logging
import re
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("consilith.settings")
HOSTNAME = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$|^localhost$"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CONSILITH_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    env: Literal["development", "test", "production", "hosted"] = "development"
    # Hosted mode (decision 22): the stack is reachable from outside this machine through a
    # tunnel. ``CONSILITH_ENV=hosted`` or ``CONSILITH_HOSTED=1`` turns it on.
    hosted: bool = False
    # The public hostname of the web gateway, such as ``consilith.example.org`` (no scheme).
    public_hostname: str = ""
    # Origins allowed to make state-changing sign-in calls. Hosted default: the public origin.
    allowed_origins: list[str] = Field(default_factory=list[str])
    # Hosted sign-in rate limits: attempts per client per minute, and invite-link lifetime.
    signin_rate_per_min: int = 10
    hosted_invite_ttl_s: int = 24 * 3600
    data_dir: Path = Path("var")
    host: str = "127.0.0.1"
    port: int = 8700
    public_url: str = "http://127.0.0.1:8700"
    token_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    token_ttl_s: int = 12 * 3600

    # Accounts (M9). The web app's origin builds every emailed link.
    web_origin: str = "http://127.0.0.1:5173"
    signin_ttl_s: int = 15 * 60
    invite_ttl_s: int = 7 * 24 * 3600
    # The dev mailbox shows emailed links on loopback only. Never in production.
    dev_mailbox: bool = False
    # Mail goes through a provider in production; none is built yet, so it refuses to send.
    mail_provider: str = ""
    mail_from: str = ""
    # GitHub sign-in is off unless all three are set.
    github_client_id: str = ""
    github_client_secret: SecretStr = SecretStr("")
    github_callback_url: str = ""

    referee_url: str = "http://127.0.0.1:8720"
    referee_public_key_path: Path | None = None  # PEM, pinned; the room never fetches it
    referee_token: str = ""  # shared with the referee; only the room may ask for reviews
    # Installed package selection is deployment configuration, never model-controlled.
    extension_modules: list[str] = Field(default_factory=list[str])
    plugin_catalog: Path | None = None
    # The deployment manifest whose ``models`` section lists the models an operator knows. None:
    # ``deployment.json`` in the working directory when there is one (never under test).
    deployment_json: Path | None = None
    solver_urls: dict[str, str] = Field(default_factory=dict[str, str])
    solver_apps: dict[str, str] = Field(default_factory=dict[str, str])

    # People who may run /solvers/discover and /solvers/warm. Empty means nobody in production;
    # outside production an empty list leaves them open to any signed-in person (dev and tests).
    operators: list[str] = Field(default_factory=list[str])

    @property
    def resolved_solver_urls(self) -> dict[str, str]:
        """MCP server URLs come from the operator catalog (``solver_urls``) only. A plugin's own
        ``servers`` section never registers a URL."""
        return dict(self.solver_urls)

    # Bring-your-own agents: an explicit allowlist of host:port endpoints that may be
    # loopback or private (dev only). Never a blanket flag.
    outbound_dev_allowlist: list[str] = Field(default_factory=list[str])

    # Sign in with ChatGPT: the local sidecar (apps/siwc) on this machine, and the shared secret
    # it requires on every request. Empty secret: the provider is unavailable.
    siwc_url: str = "http://127.0.0.1:8730"
    siwc_secret: str = ""

    # Agent-platform feature flags (BUILD_SPEC section 10). Each is off by default, and each stays
    # off at runtime until its security gate stage is green (``loop_enabled``, ``peers_enabled``).
    agent_loop: bool = False  # CONSILITH_AGENT_LOOP: the tool-using agent loop (gate stage 1)
    peers: bool = False  # CONSILITH_PEERS: agent-to-agent messages (gate stage 2)
    siwc_multi: bool = False  # CONSILITH_SIWC_MULTI: one ChatGPT plan per person
    # The build writes security_gate.json (tools/security_gate.py); None means nothing has passed.
    security_gate_path: Path | None = None

    feed_rotate_s: float = 600.0
    keepalive_s: float = 15.0
    presence_away_s: float = 15.0

    @property
    def is_hosted(self) -> bool:
        return self.hosted or self.env == "hosted"

    @property
    def cookie_secure(self) -> bool:
        """Cookies are Secure whenever the app is served over https, and always when hosted."""
        return self.is_hosted or self.web_origin.startswith("https://")

    @property
    def origins(self) -> frozenset[str]:
        """The origins a hosted sign-in call may come from (empty outside hosted mode)."""
        if not self.is_hosted:
            return frozenset()
        return frozenset(o.rstrip("/").lower() for o in [self.web_origin, *self.allowed_origins])

    @property
    def github_enabled(self) -> bool:
        return bool(
            self.github_client_id
            and self.github_client_secret.get_secret_value()
            and self.github_callback_url
        )

    @model_validator(mode="after")
    def _hosted_fails_closed(self) -> Settings:
        if not self.is_hosted:
            return self
        host = self.public_hostname.strip().lower()
        if not HOSTNAME.match(host):
            raise ValueError("CONSILITH_PUBLIC_HOSTNAME is required when hosted (a host name only)")
        if host == "localhost" or host.startswith("127.") or host in {"0.0.0.0"}:
            raise ValueError("CONSILITH_PUBLIC_HOSTNAME must be the public hostname")
        origin = f"https://{host}"
        if self.web_origin == type(self).model_fields["web_origin"].default:
            self.web_origin = origin
        if self.web_origin.rstrip("/").lower() != origin:
            raise ValueError("CONSILITH_WEB_ORIGIN must be https://<CONSILITH_PUBLIC_HOSTNAME>")
        # The allowed-origin list is the public hostname and whatever is added explicitly.
        for extra in self.allowed_origins:
            if not extra.startswith("https://"):
                raise ValueError("CONSILITH_ALLOWED_ORIGINS holds https origins only when hosted")
        self.public_hostname = host
        if self.dev_mailbox:
            raise ValueError("CONSILITH_DEV_MAILBOX must be off when hosted")
        if self.outbound_dev_allowlist:
            raise ValueError("CONSILITH_OUTBOUND_DEV_ALLOWLIST must be empty when hosted")
        if len(self.referee_token) < 16:
            raise ValueError("CONSILITH_REFEREE_TOKEN is required when hosted")
        if self.referee_public_key_path is None:
            raise ValueError("CONSILITH_REFEREE_PUBLIC_KEY_PATH is required when hosted")
        if self.signin_rate_per_min < 1:
            raise ValueError("CONSILITH_SIGNIN_RATE_PER_MIN must be at least 1")
        if not any(o.strip() for o in self.operators):
            raise ValueError("CONSILITH_OPERATORS is required when hosted (the operator routes)")
        # Both security gates are required, whatever the feature flags say.
        problems = hosted_gate_problems(self)
        if problems:
            raise ValueError("hosted mode needs both security gates green: " + "; ".join(problems))
        return self

    @model_validator(mode="after")
    def _production_fails_closed(self) -> Settings:
        if self.env == "production":
            if self.dev_mailbox:
                raise ValueError("CONSILITH_DEV_MAILBOX must be off in production")
            if not self.mail_provider:
                raise ValueError("CONSILITH_MAIL_PROVIDER is required in production")
            if self.outbound_dev_allowlist:
                raise ValueError("CONSILITH_OUTBOUND_DEV_ALLOWLIST must be empty in production")
            if len(self.referee_token) < 16:
                raise ValueError("CONSILITH_REFEREE_TOKEN is required in production")
            if self.referee_public_key_path is None:
                raise ValueError("CONSILITH_REFEREE_PUBLIC_KEY_PATH is required in production")
        return self


def hosted_gate_problems(settings: Settings) -> list[str]:
    """What keeps stage 1 or stage 2 of the security gate from being green, run as production
    runs it (topology check included). Empty means both are green."""
    from . import security_gate  # deferred: the gate module imports ``Settings`` from here

    gate = security_gate.SecurityGate.load(
        settings.security_gate_path,
        env="production",
        extension_modules=settings.extension_modules,
        plugin_catalog=settings.plugin_catalog,
    )
    out: list[str] = [*gate.problems]
    out += [f"self-test {t.name} failed" for t in gate.failed_self_tests()]
    for stage in (1, 2):
        if gate.missing(stage):
            out.append(f"stage {stage} not passed: " + ", ".join(gate.missing(stage)))
    return out


def _gate_green(settings: Settings, stage: int) -> bool:
    # Imported here: the gate module imports ``Settings`` from this one.
    from . import security_gate

    return security_gate.stage_passed(
        stage,
        settings.security_gate_path,
        env=settings.env,
        extension_modules=settings.extension_modules,
        plugin_catalog=settings.plugin_catalog,
    )


def loop_enabled(settings: Settings) -> bool:
    """The agent loop runs only when its flag is on and security gate stage 1 is green."""
    return settings.agent_loop and _gate_green(settings, 1)


def peers_enabled(settings: Settings) -> bool:
    """Agent-to-agent messages run only when their flag is on and gate stage 2 is green."""
    return settings.peers and _gate_green(settings, 2)


def warn_closed_gates(settings: Settings) -> dict[str, bool]:
    """Resolve both gated flags once, at startup. A flag that is on while its gate is not green
    is logged as a warning and the feature stays off. Returns what is enabled."""
    state = {"agent_loop": loop_enabled(settings), "peers": peers_enabled(settings)}
    for flag, env_name in (("agent_loop", "CONSILITH_AGENT_LOOP"), ("peers", "CONSILITH_PEERS")):
        if getattr(settings, flag) and not state[flag]:
            log.warning(
                "%s is on but its security gate is not green; the feature stays off", env_name
            )
    return state


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
