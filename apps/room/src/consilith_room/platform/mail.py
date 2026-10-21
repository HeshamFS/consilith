"""Mail is a port: the room hands over an address and a link, never a transport.

- ``DevMailbox`` keeps messages in memory so a developer can read the link (loopback only,
  see ``GET /dev/mailbox``);
- ``ProviderMailer`` is the production placeholder: no provider is built yet, so it refuses to
  send, and ``build_mailer`` refuses to start in production without one configured.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Protocol

from .clock import iso
from .settings import Settings


class MailError(RuntimeError):
    """The message could not be sent."""


class Mailer(Protocol):
    def send(self, to: str, subject: str, url: str) -> None: ...


class DevMailbox:
    """In memory only: gone on restart, and never present in production."""

    def __init__(self, limit: int = 200) -> None:
        self._box: deque[dict[str, object]] = deque(maxlen=limit)
        self._lock = threading.Lock()

    def send(self, to: str, subject: str, url: str) -> None:
        with self._lock:
            self._box.append({"to": to, "url": url, "at": iso(time.time())})

    def messages(self) -> list[dict[str, object]]:
        """Newest first."""
        with self._lock:
            return list(reversed(self._box))


class ProviderMailer:
    """The production seam. No provider adapter exists yet; sending says so instead of
    pretending."""

    def __init__(self, provider: str) -> None:
        self.provider = provider

    def send(self, to: str, subject: str, url: str) -> None:
        raise MailError(f"mail provider {self.provider!r} has no adapter yet")


class NoMailer:
    """Development without a mailbox or a provider: nothing can be sent."""

    def send(self, to: str, subject: str, url: str) -> None:
        raise MailError("no mail provider; start with CONSILITH_DEV_MAILBOX=1 to read links")


def build_mailer(settings: Settings) -> Mailer:
    if settings.env == "production":
        if settings.dev_mailbox:
            raise MailError("the dev mailbox is never served in production")
        if not settings.mail_provider:
            raise MailError("production needs a mail provider (CONSILITH_MAIL_PROVIDER)")
        return ProviderMailer(settings.mail_provider)
    if settings.dev_mailbox:
        return DevMailbox()
    if settings.mail_provider:
        return ProviderMailer(settings.mail_provider)
    return NoMailer()
