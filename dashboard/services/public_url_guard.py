"""Public-URL guard for server-side fetches of caller-supplied URLs (SSRF).

``check_public_url`` resolves the host and rejects the URL unless every
resolved address is globally routable. It catches the forms a string check
misses: decimal / hex / short IPv4 (``2130706433``, ``0x7f000001``,
``127.1``), wildcard DNS names (``127.0.0.1.nip.io``), CGNAT, benchmark and
other non-global ranges, and IPv4-mapped IPv6.

``httpx_request_guard`` is an httpx ``request`` event hook. httpx runs it for
every request it sends, redirect hops included, so a public URL that
redirects to an internal address is refused before the hop is sent.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Optional
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
    try:
        addrs = await asyncio.wait_for(
            asyncio.to_thread(_resolve, host, port), timeout=_RESOLVE_TIMEOUT_S
        )
    except (asyncio.TimeoutError, OSError, UnicodeError, ValueError):
        return "URL host could not be resolved"
    if not addrs:
        return "URL host could not be resolved"
    if not all(_is_public_ip(a) for a in addrs):
        return "URL host is not a public address"
    return None


async def httpx_request_guard(request) -> None:
    """httpx request event hook: refuse any hop (incl. redirects) to a non-public host."""
    err = await check_public_url(str(request.url))
    if err:
        raise UnsafeURLError(err)
