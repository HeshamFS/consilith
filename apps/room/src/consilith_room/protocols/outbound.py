"""One outbound policy for brought agents: discovery and every later A2A call (SPEC "A2A").

- The card URL starts with ``https://`` and ends with ``/.well-known/agent-card.json``.
- Every interface URL the card names passes the same checks.
- Redirects are never followed.
- DNS is resolved when the request is made; loopback, private, link-local, multicast and
  reserved addresses (IPv4, IPv6 and IPv4-mapped IPv6) are refused. The connection then goes to
  the checked address itself, for HTTPS too: the hostname is kept for TLS (SNI and certificate
  checks) and the Host header, so a second lookup cannot rebind it to an internal service.
- Every response is capped in bytes and the whole exchange in time, DNS lookup included.
  Requests ask for an uncompressed body and any encoded response is refused, so the cap
  counts the bytes the caller will actually read.
- The dev exception is an explicit allowlist of ``host:port`` endpoints (they may be loopback
  and plain HTTP), never a blanket flag.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

CARD_SUFFIX = "/.well-known/agent-card.json"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
TOTAL_DEADLINE_S = 60.0


class OutboundRefused(ValueError):
    """The URL, the address it resolves to, or the response is not allowed."""


def _public(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


class OutboundPolicy:
    def __init__(
        self,
        dev_allowlist: list[str] | None = None,
        *,
        max_bytes: int = MAX_RESPONSE_BYTES,
        deadline_s: float = TOTAL_DEADLINE_S,
    ) -> None:
        self.allow = {a.strip().lower() for a in dev_allowlist or [] if a.strip()}
        self.max_bytes = max_bytes
        self.deadline_s = deadline_s

    def _allowlisted(self, host: str, port: int) -> bool:
        return f"{host.lower()}:{port}" in self.allow

    def check_url(self, url: str, *, card: bool = False) -> None:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if not host:
            raise OutboundRefused(f"{url!r} has no host")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        listed = self._allowlisted(host, port)
        if parts.scheme != "https" and not (listed and parts.scheme == "http"):
            raise OutboundRefused(f"{url!r}: only https is allowed")
        if parts.username or parts.password:
            raise OutboundRefused(f"{url!r}: credentials in URLs are not allowed")
        if card and not parts.path.endswith(CARD_SUFFIX):
            raise OutboundRefused(f"{url!r} must end with {CARD_SUFFIX}")

    def resolve(self, host: str, port: int) -> list[str]:
        """The addresses ``host`` resolves to now; refused if any is not public."""
        if self._allowlisted(host, port):
            return [host]
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise OutboundRefused(f"{host} does not resolve") from exc
        addrs = sorted({str(info[4][0]) for info in infos})
        for addr in addrs:
            if not _public(addr):
                raise OutboundRefused(f"{host} resolves to a non-public address {addr}")
        return addrs

    def client(self, timeout_s: float = 60.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=GuardedTransport(self),
            timeout=min(timeout_s, self.deadline_s),
            follow_redirects=False,
        )


class GuardedTransport(httpx.AsyncBaseTransport):
    def __init__(self, policy: OutboundPolicy) -> None:
        self.policy = policy
        self.inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            async with asyncio.timeout(self.policy.deadline_s):
                return await self._send(request)
        except TimeoutError as exc:
            raise OutboundRefused(
                f"{request.url} took longer than {self.policy.deadline_s:g} s"
            ) from exc

    async def _send(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.policy.check_url(str(url))
        port = url.port or (443 if url.scheme == "https" else 80)
        host = url.host
        addrs = await asyncio.to_thread(self.policy.resolve, host, port)  # inside the deadline
        request.headers["Accept-Encoding"] = "identity"
        if addrs and addrs[0] != host:
            ip = addrs[0]
            request.url = url.copy_with(host=ip)  # connect to the address that was checked
            request.headers["Host"] = url.netloc.decode("ascii")
            if url.scheme == "https":
                request.extensions = {
                    **request.extensions,
                    "sni_hostname": host,
                }  # TLS keeps the name
        response = await self.inner.handle_async_request(request)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise OutboundRefused(f"{url} answered with a redirect; redirects are not followed")
        encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
        if encoding not in ("", "identity"):
            await response.aclose()
            raise OutboundRefused(f"{url} answered with {encoding} encoding; only identity is read")
        body = bytearray()
        stream = response.stream
        assert isinstance(stream, httpx.AsyncByteStream)
        try:
            async for chunk in stream:
                body += chunk
                if len(body) > self.policy.max_bytes:
                    raise OutboundRefused(
                        f"{url} answered with more than {self.policy.max_bytes} bytes"
                    )
        finally:
            await response.aclose()
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=bytes(body),
            request=request,
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self.inner.aclose()
