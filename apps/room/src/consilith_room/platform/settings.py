"""The one place the room server reads its environment (stack rule 4)."""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CONSILITH_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    env: Literal["development", "test", "production"] = "development"
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
    solver_urls: dict[str, str] = Field(default_factory=dict[str, str])
    solver_apps: dict[str, str] = Field(default_factory=dict[str, str])

    @property
    def resolved_solver_urls(self) -> dict[str, str]:
        from consilith_contracts.extensions import all_extensions

        return {**all_extensions("servers"), **self.solver_urls}

    # Bring-your-own agents: an explicit allowlist of host:port endpoints that may be
    # loopback or private (dev only). Never a blanket flag.
    outbound_dev_allowlist: list[str] = Field(default_factory=list[str])

    # Sign in with ChatGPT: the local sidecar (apps/siwc) on this machine, and the shared secret
    # it requires on every request. Empty secret: the provider is unavailable.
    siwc_url: str = "http://127.0.0.1:8730"
    siwc_secret: str = ""

    feed_rotate_s: float = 600.0
    keepalive_s: float = 15.0
    presence_away_s: float = 15.0

    @property
    def github_enabled(self) -> bool:
        return bool(
            self.github_client_id
            and self.github_client_secret.get_secret_value()
            and self.github_callback_url
        )

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
