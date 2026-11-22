"""Public-URL guard for server-side fetches of caller-supplied URLs (SSRF).

``check_public_url`` resolves the host and rejects the URL unless every
resolved address is globally routable. It catches the forms a string check
misses: decimal / hex / short IPv4 (``2130706433``, ``0x7f000001``,
``127.1``), wildcard DNS names (``127.0.0.1.nip.io``), CGNAT, benchmark and
other non-global ranges, and IPv4-mapped IPv6.

``PinnedPublicTransport`` is the httpx transport for those fetches. For every
request httpx sends, redirect hops included, it resolves the host once,
requires every address to be public, and connects to that checked address.
The URL host is swapped for the IP; the ``Host`` header keeps the original
name and TLS uses it for SNI and, when ``verify`` is on, for certificate
hostname checks. A DNS answer that changes between the check and the connect
(rebinding) cannot move the connection to an internal address, because the
connection never resolves the name again.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpx
from typing import Callable, Optional
from urllib.parse import urlsplit

_RESOLVE_TIMEOUT_S = 5.0
_BLOCKED_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".home.arpa")


class UnsafeURLError(ValueError):
    """Raised when a URL points at a non-public address."""


def _resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _is_public_ip(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(ip.is_global) and not ip.is_multicast


async def check_public_url(raw_url: str) -> Optional[str]:
    """Return an error string when the URL is not a public http(s) URL, else None."""
    try:
        parsed = urlsplit((raw_url or "").strip())
        port = parsed.port
    except ValueError:
        return "Malformed URL"
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return "URL must use http or https"
    if parsed.username is not None or parsed.password is not None:
        return "URL must not carry credentials"
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return "URL is missing a hostname"
    if host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
        return "URL host is not allowed"
    if port is None:
        port = 443 if scheme == "https" else 80
    _addrs, err = await resolve_public_addresses(host, port)
    return err


async def resolve_public_addresses(host: str, port: int) -> tuple[list[str], Optional[str]]:
    """Resolve ``host`` once. Return (addresses, None) only when every address is public."""
    try:
        addrs = await asyncio.wait_for(
            asyncio.to_thread(_resolve, host, port), timeout=_RESOLVE_TIMEOUT_S
        )
    except (asyncio.TimeoutError, OSError, UnicodeError, ValueError):
        return [], "URL host could not be resolved"
    addrs = [a.split("%", 1)[0] for a in (addrs or [])]
    if not addrs:
        return [], "URL host could not be resolved"
    if not all(_is_public_ip(a) for a in addrs):
        return [], "URL host is not a public address"
    return addrs, None


class PinnedPublicTransport(httpx.AsyncBaseTransport):
    """httpx transport that connects only to a validated public IP for each request.

    httpx calls ``handle_async_request`` once per hop, so every redirect target
    is resolved, validated and pinned on its own. The request URL is rewritten
    to the first checked address, the original ``Host`` header is kept, and the
    original host name is passed as ``sni_hostname`` so TLS SNI and certificate
    hostname checks (when ``verify`` is on) use it. One inner transport is kept
    per (host, ip, port) so a TLS connection opened for one name is never
    reused for another name on the same IP.
    """

    def __init__(
        self,
        *,
        verify: bool = True,
        inner_factory: Optional[Callable[[], httpx.AsyncBaseTransport]] = None,
    ) -> None:
        self._verify = verify
        self._inner_factory = inner_factory or (lambda: httpx.AsyncHTTPTransport(verify=self._verify))
        self._inner: dict[tuple[str, str, int], httpx.AsyncBaseTransport] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        scheme = (url.scheme or "").lower()
        if scheme not in ("http", "https"):
            raise UnsafeURLError("URL must use http or https")
        if url.username or url.password:
            raise UnsafeURLError("URL must not carry credentials")
        host = (url.host or "").strip().lower().rstrip(".")
        if not host or host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
            raise UnsafeURLError("URL host is not allowed")
        port = url.port or (443 if scheme == "https" else 80)
        addrs, err = await resolve_public_addresses(host, port)
        if err:
            raise UnsafeURLError(err)
        ip = addrs[0]
        if not _is_public_ip(ip):  # defensive; resolve_public_addresses already checked
            raise UnsafeURLError("URL host is not a public address")

        extensions = dict(request.extensions)
        extensions["sni_hostname"] = host
        pinned = httpx.Request(
            request.method,
            url.copy_with(host=ip),
            headers=request.headers,  # Host header still names the original host
            stream=request.stream,
            extensions=extensions,
        )
        key = (host, ip, port)
        inner = self._inner.get(key)
        if inner is None:
            inner = self._inner[key] = self._inner_factory()
        response = await inner.handle_async_request(pinned)
        response.request = request
        return response

    async def aclose(self) -> None:
        inners, self._inner = list(self._inner.values()), {}
        for inner in inners:
            await inner.aclose()
