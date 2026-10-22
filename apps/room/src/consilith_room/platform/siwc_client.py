"""The local Sign in with ChatGPT sidecar client, and the person-to-client lookup."""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING, Any, cast

from .errors import NotConfigured

if TYPE_CHECKING:
    from .store import Store

# Sign-in addresses must be https on one of these origins unless the bridge declares others in
# ``GET /v2/capabilities``. The page opens this address in a new tab, so it must not be a script
# or a look-alike host.
DEFAULT_AUTH_ORIGINS = ("https://auth.openai.com",)


def account_key(secret: str, person: str) -> str:
    """The bridge's name for a person's ChatGPT account: an HMAC of the person id under the
    shared secret (``apps/siwc/src/accounts.ts``, ``accountKeyFor``). Same value on both sides."""
    mac = hmac.new(secret.encode(), b"siwc-account:" + person.encode(), hashlib.sha256)
    return mac.hexdigest()[:32]


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
        # CONSILITH_SIWC_MULTI: one ChatGPT plan per person, through ``/v2/accounts/<key>/...``.
        # Off, the helper holds one sign-in and ``owner`` is the person it belongs to.
        self.multi = False
        self._legacy_bound = False
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

    def for_account(self, person: str) -> SiwcAccount:
        """This person's account on the bridge (``multi`` only)."""
        return SiwcAccount(self, person)

    async def bridge_capabilities(self) -> dict[str, Any]:
        """What the bridge says about itself (``GET /v2/capabilities``). Empty when it cannot."""
        try:
            return await self.call("GET", "/v2/capabilities")
        except NotConfigured:
            return {}

    async def ensure_legacy_bound(self, person: str) -> None:
        """The person who signed in before several accounts existed keeps that sign-in: tell the
        bridge once that their key maps to the old directory. Nobody else is affected."""
        if self._legacy_bound or self.owner != person:
            return
        key = account_key(self._secret, person)
        await self.call("POST", f"/v2/accounts/{key}/bind-legacy")
        self._legacy_bound = True

    def __repr__(self) -> str:
        return f"SiwcClient(url={self.url!r}, secret=[redacted])"

    async def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        import httpx

        if not self._secret:
            raise NotConfigured("Sign in with ChatGPT is not set up on this server")
        try:
            # The DevKit bounds response streaming at180s; allow its safe error to return.
            async with httpx.AsyncClient(timeout=190 if path.endswith("/reply") else 60) as client:
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
        body_out = cast("dict[str, Any]", data)
        if resp.status_code >= 400:
            err: Any = body_out.get("error", {})
            code: Any = cast("dict[str, Any]", err).get("code") if isinstance(err, dict) else None
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
                "sign_in_busy": "Someone else is signing in. Try again in a minute.",
                "sign_in_rate_limited": "Too many sign-in attempts. Try again in a few minutes.",
                "already_signed_in": "This ChatGPT account is already connected.",
                "identity_in_use": (
                    "That ChatGPT account is already connected to another person. "
                    "Use a private window or sign out of ChatGPT first, then try again."
                ),
                "identity_unknown": (
                    "This ChatGPT sign-in could not be told apart from another connected "
                    "account, so it was refused."
                ),
                "account_mismatch": (
                    "This sign-in was for a different ChatGPT account than before. "
                    "Choose the original account, or switch accounts."
                ),
                "sign_in_expired": "The sign-in took too long and was cancelled. Try again.",
                "no_sign_in": "There is no sign-in waiting for you. Start one first.",
                "invalid_callback": "That address does not belong to your sign-in.",
                "callback_used": "That sign-in address was already used.",
                "callback_rejected": (
                    "The sign-in address was not accepted. Start the sign-in again."
                ),
                "paste_unavailable": "Pasting the sign-in address is not available here.",
                "multi_disabled": "Several ChatGPT accounts are not enabled on this server.",
            }
            # Only fixed, known messages leave this boundary. Never reflect an arbitrary
            # upstream body, even if a provider happens to include prompts or credentials.
            if isinstance(code, str) and code in safe_errors:
                raise NotConfigured(safe_errors[code], code=code)
            raise NotConfigured("The ChatGPT provider refused the request.", code="provider_error")
        return body_out


class SiwcAccount(SiwcClient):
    """One person's ChatGPT account on the bridge. It speaks the same ``call(method, path)`` as
    ``SiwcClient`` for ``/status``, ``/login``, ``/logout``, ``/models`` and ``/reply``, and sends
    each to ``/v2/accounts/<key>/...``, so a model adapter does not know which kind it holds."""

    def __init__(self, parent: SiwcClient, person: str) -> None:
        super().__init__(parent.url, parent._secret)  # pyright: ignore[reportPrivateUsage]
        self._parent = parent
        self.owner = person
        self.multi = True
        self.key = account_key(parent._secret, person)  # pyright: ignore[reportPrivateUsage]

    def __repr__(self) -> str:
        return f"SiwcAccount(key={self.key!r})"

    async def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        if self.owner is not None:
            await self._parent.ensure_legacy_bound(self.owner)
        if path.startswith("/v2/"):
            return await super().call(method, path, body)
        return await super().call(method, f"/v2/accounts/{self.key}{path}", body)


# People who left their last room and have not yet acted on the ChatGPT disconnect offer. The
# Connect panel shows the offer; signing out clears it.
LEAVE_OFFERS: set[str] = set()


def _offer_on_leave(store: Store, room: str, person: str) -> None:
    """When a person's last seat goes, offer to disconnect their ChatGPT account. The account is
    never removed without their say-so, because they may still want it for another room."""
    del room
    if not store.seats_of(person):
        LEAVE_OFFERS.add(person)


def _register_leave_offer() -> None:
    from .stores import register_leave_hook

    register_leave_hook(_offer_on_leave)


_register_leave_offer()


def leave_offer_pending(store: Store, person: str) -> bool:
    """Whether the disconnect offer is open: they left every room and are not seated again."""
    if person not in LEAVE_OFFERS:
        return False
    if store.seats_of(person):
        LEAVE_OFFERS.discard(person)
        return False
    return True


class SiwcAccounts:
    """Which ChatGPT client belongs to a person. With ``multi`` off there is one client and one
    owner. With it on, every person has an account on the bridge, found by their key."""

    def __init__(self, client: SiwcClient | None, *, multi: bool | None = None) -> None:
        self._client = client
        self._multi = multi

    @property
    def multi(self) -> bool:
        if self._multi is not None:
            return self._multi
        return bool(self._client is not None and self._client.multi)

    def for_person(self, person: str) -> SiwcClient | None:
        client = self._client
        if client is None or not client.available:
            return None
        if self.multi:
            return client.for_account(person)
        if client.owner == person:
            return client
        return None
