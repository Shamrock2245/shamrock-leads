"""
Shared SOCKS proxy helpers for Cloudflare-protected Revize scrapers.

Production path: office iMac → ssh reverse tunnel → VPS :1080
Docker containers reach host SOCKS via bridge gateway 172.18.0.1:1080.

If CONNECT fails (stale tunnel), scrapers should fail loudly with a clear
error rather than spinning on ERR_SOCKS_CONNECTION_FAILED.
"""
from __future__ import annotations

import logging
import os
import socket
import struct
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_SOCKS = "socks5://172.18.0.1:1080"


def get_socks_proxy_url() -> str:
    """Resolve SOCKS URL from env or default Docker-bridge address."""
    return (
        os.environ.get("SCRAPER_SOCKS_PROXY")
        or os.environ.get("SOCKS_PROXY")
        or DEFAULT_SOCKS
    ).strip()


def _parse_socks_host_port(proxy_url: str) -> tuple[str, int]:
    # socks5://host:port or socks5h://host:port
    u = proxy_url.strip()
    for prefix in ("socks5h://", "socks5://", "socks4://"):
        if u.lower().startswith(prefix):
            u = u[len(prefix) :]
            break
    if "@" in u:
        u = u.split("@", 1)[1]
    host, _, port_s = u.partition(":")
    port = int(port_s or "1080")
    return host, port


def socks5_connect_ok(
    proxy_url: Optional[str] = None,
    test_host: str = "example.com",
    test_port: int = 443,
    timeout: float = 5.0,
) -> bool:
    """
    True if SOCKS5 greeting + CONNECT succeed.
    A listening port that only greets but drops CONNECT is treated as unhealthy.
    """
    proxy_url = proxy_url or get_socks_proxy_url()
    try:
        host, port = _parse_socks_host_port(proxy_url)
    except Exception as e:
        logger.warning("[SOCKS] bad proxy URL %s: %s", proxy_url, e)
        return False

    try:
        s = socket.socket()
        s.settimeout(timeout)
        s.connect((host, port))
        # greeting: VER=5, NMETHODS=1, METHOD=0 (no auth)
        s.sendall(b"\x05\x01\x00")
        greet = s.recv(2)
        if greet != b"\x05\x00":
            logger.warning("[SOCKS] bad greeting from %s:%s → %r", host, port, greet)
            s.close()
            return False
        # CONNECT domain
        req = (
            b"\x05\x01\x00\x03"
            + bytes([len(test_host)])
            + test_host.encode("ascii")
            + struct.pack("!H", test_port)
        )
        s.sendall(req)
        resp = s.recv(10)
        s.close()
        if not resp or len(resp) < 2 or resp[1] != 0:
            logger.warning(
                "[SOCKS] CONNECT failed via %s:%s (resp=%r). "
                "Office reverse tunnel is likely stale — restart iMac→VPS SOCKS "
                "(ssh -R 0.0.0.0:1080 → local SOCKS).",
                host,
                port,
                resp,
            )
            return False
        return True
    except Exception as e:
        logger.warning("[SOCKS] health check failed %s: %s", proxy_url, e)
        return False


def require_socks_or_raise(proxy_url: Optional[str] = None) -> str:
    """Return proxy URL if healthy, else raise RuntimeError with ops guidance."""
    url = proxy_url or get_socks_proxy_url()
    if socks5_connect_ok(url):
        return url
    raise RuntimeError(
        f"SOCKS proxy unhealthy ({url}). "
        "Charlotte/Manatee/Sarasota require the office residential tunnel on VPS :1080. "
        "On office iMac: ensure local SOCKS is up and LaunchAgent reverse-SSH "
        "`ssh -R 0.0.0.0:1080:127.0.0.1:<local_socks_port> root@VPS` is connected."
    )
