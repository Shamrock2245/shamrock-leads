"""
Shared residential / SOCKS proxy helpers for Cloudflare-protected scrapers.

Resolution order (prefer APE/Warren, fall back to office tunnel):
  1. Explicit ``SCRAPER_SOCKS_PROXY`` / ``SOCKS_PROXY`` env if healthy
  2. Autonomous Proxy Engine (Warren residential → S5W2C → Stormsia)
  3. Office SOCKS candidates: env default, ``127.0.0.1:1080``, Docker bridge

If all paths fail, scrapers should fail loudly with ops guidance rather than
spinning on ERR_SOCKS_CONNECTION_FAILED.
"""
from __future__ import annotations

import logging
import os
import socket
import struct
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

# Docker containers on VPS reach host reverse-SSH tunnel via bridge gateway
DEFAULT_SOCKS = "socks5://172.18.0.1:1080"
# Local Mac (or host) office SOCKS if reverse tunnel is terminated here
LOCAL_SOCKS = "socks5://127.0.0.1:1080"


def get_socks_proxy_url() -> str:
    """Resolve SOCKS URL from env or default Docker-bridge address."""
    return (
        os.environ.get("SCRAPER_SOCKS_PROXY")
        or os.environ.get("SOCKS_PROXY")
        or DEFAULT_SOCKS
    ).strip()


def _normalize_playwright_proxy(proxy_url: str) -> str:
    """Playwright expects socks5://host:port (not socks5h)."""
    u = (proxy_url or "").strip()
    if u.lower().startswith("socks5h://"):
        return "socks5://" + u[len("socks5h://") :]
    if "://" not in u and u:
        return f"socks5://{u}"
    return u


def to_playwright_proxy(proxy_url: str) -> Dict[str, str]:
    """Build a Playwright ``browser.launch(proxy=...)`` dict.

    Playwright does not reliably parse ``user:pass@host`` inside ``server``.
    Split credentials into username/password fields for HTTP and SOCKS proxies.
    """
    raw = _normalize_playwright_proxy(proxy_url)
    if not raw:
        raise ValueError("empty proxy_url")

    # Ensure scheme for urlparse
    if "://" not in raw:
        raw = f"socks5://{raw}"

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "socks5").lower()
    host = parsed.hostname or ""
    port = parsed.port
    if not host:
        # Fallback: treat whole string as server
        return {"server": raw}

    if port:
        server = f"{scheme}://{host}:{port}"
    else:
        server = f"{scheme}://{host}"

    out: Dict[str, str] = {"server": server}
    if parsed.username:
        out["username"] = unquote(parsed.username)
    if parsed.password:
        out["password"] = unquote(parsed.password)
    return out


def to_httpx_proxy(proxy_url: str) -> str:
    """Return proxy URL suitable for httpx ``proxy=`` (credentials in URL OK)."""
    return _normalize_playwright_proxy(proxy_url)


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
    timeout: float = 3.0,
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


def tcp_connect_ok(host: str, port: int, timeout: float = 3.0) -> bool:
    """Quick TCP reachability check (used for Warren hub HTTP proxies)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_proxy_endpoint_ok(proxy_url: str, timeout: float = 3.0) -> bool:
    """True if the host:port of an HTTP(S) proxy URL accepts TCP connections."""
    try:
        raw = proxy_url if "://" in proxy_url else f"http://{proxy_url}"
        parsed = urlparse(raw)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            return False
        return tcp_connect_ok(host, port, timeout=timeout)
    except Exception as e:
        logger.warning("[proxy] HTTP proxy endpoint check failed: %s", e)
        return False


def require_socks_or_raise(proxy_url: Optional[str] = None) -> str:
    """Return proxy URL if healthy, else raise RuntimeError with ops guidance.

    Prefer :func:`resolve_residential_proxy` when an APE-aware scraper is
    available — this entry point only checks the legacy SOCKS tunnel path.
    """
    url = proxy_url or get_socks_proxy_url()
    if socks5_connect_ok(url):
        return _normalize_playwright_proxy(url)
    raise RuntimeError(
        f"SOCKS proxy unhealthy ({url}). "
        "Charlotte/Manatee/Sarasota require residential egress "
        "(APE/Warren or office tunnel on VPS :1080). "
        "On office iMac: ensure Warren node (com.warren.node) is running, or "
        "restore reverse-SSH SOCKS to VPS :1080."
    )


def _office_socks_candidates() -> List[str]:
    """Ordered list of office SOCKS URLs to try (unique, non-empty)."""
    candidates = [
        os.environ.get("SCRAPER_SOCKS_PROXY") or "",
        os.environ.get("SOCKS_PROXY") or "",
        LOCAL_SOCKS,
        DEFAULT_SOCKS,
    ]
    seen = set()
    out: List[str] = []
    for c in candidates:
        c = c.strip()
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def resolve_residential_proxy(
    scraper: Any = None,
    *,
    prefer_residential: bool = True,
    require: bool = True,
    sticky_session: Optional[str] = None,
) -> Tuple[Optional[str], str]:
    """Resolve a proxy for Cloudflare / WAF-protected FL scrapers.

    Order:
      1. Env SOCKS if healthy (ops override)
      2. APE via ``scraper.get_proxy`` / sticky session (Warren residential preferred)
      3. Office SOCKS candidates (local + Docker bridge)

    Args:
        scraper: Optional BaseScraper instance (uses ``get_proxy`` / APE metrics).
        prefer_residential: Passed to APE.
        require: If True, raise RuntimeError when nothing healthy is available.
        sticky_session: When set, prefer APE sticky routing for multi-page CF flows.

    Returns:
        ``(proxy_url, source)`` where source is ``env_socks`` | ``ape`` | ``office_socks``.
        proxy_url keeps credentials in URL form for httpx; use
        :func:`to_playwright_proxy` for Playwright.
    """
    # 1) Explicit env SOCKS — ops pin (only if set)
    env_url = (
        os.environ.get("SCRAPER_SOCKS_PROXY") or os.environ.get("SOCKS_PROXY") or ""
    ).strip()
    if env_url and socks5_connect_ok(env_url):
        logger.info("[proxy] using env SOCKS (%s)", env_url.split("@")[-1])
        return _normalize_playwright_proxy(env_url), "env_socks"

    # 2) APE (Warren residential preferred)
    ape_proxy = None
    if scraper is not None:
        try:
            if sticky_session and hasattr(scraper, "get_sticky_proxy"):
                ape_proxy = scraper.get_sticky_proxy(sticky_session)
            if not ape_proxy and hasattr(scraper, "get_proxy"):
                ape_proxy = scraper.get_proxy(prefer_residential=prefer_residential)
        except Exception as exc:
            logger.warning("[proxy] APE get_proxy failed: %s", exc)
            ape_proxy = None

    if ape_proxy:
        normalized = _normalize_playwright_proxy(ape_proxy)
        ape_ok = False
        if normalized.lower().startswith("socks"):
            ape_ok = socks5_connect_ok(normalized)
            if not ape_ok:
                logger.warning(
                    "[proxy] APE SOCKS failed health check: %s",
                    normalized.split("@")[-1],
                )
        else:
            # HTTP Warren — hub must be up AND exit must be residential US
            if not http_proxy_endpoint_ok(normalized):
                logger.warning(
                    "[proxy] APE HTTP endpoint unreachable: %s",
                    normalized.split("@")[-1],
                )
            else:
                try:
                    from scrapers.cf_browser import check_exit_ip

                    exit_info = check_exit_ip(normalized, timeout=15.0, retries=2)
                    if exit_info.get("residential_likely"):
                        ape_ok = True
                        logger.info(
                            "[proxy] using APE residential ip=%s org=%s",
                            exit_info.get("ip"),
                            exit_info.get("org"),
                        )
                    else:
                        logger.warning(
                            "[proxy] APE exit not residential (ip=%s org=%s country=%s) — trying fallbacks",
                            exit_info.get("ip"),
                            exit_info.get("org"),
                            exit_info.get("country"),
                        )
                except Exception as exc:
                    logger.warning("[proxy] APE exit check error: %s — trying fallbacks", exc)

        if ape_ok:
            return normalized, "ape"
        if hasattr(scraper, "record_proxy_failure"):
            try:
                scraper.record_proxy_failure(ape_proxy)
            except Exception:
                pass

    # 3) Office SOCKS candidates (local host + Docker bridge)
    for candidate in _office_socks_candidates():
        if socks5_connect_ok(candidate):
            logger.info("[proxy] using office SOCKS tunnel (%s)", candidate)
            return _normalize_playwright_proxy(candidate), "office_socks"

    # 4) Direct egress when this host already has US residential IP
    #    (office Mac on home ISP — no VPN). Hetzner VPS will fail the
    #    residential check and must not use this path for CF sites.
    allow_direct = os.environ.get("SCRAPER_ALLOW_DIRECT", "true").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if allow_direct:
        try:
            from scrapers.cf_browser import check_exit_ip

            direct = check_exit_ip(None, timeout=12.0, retries=2)
            if direct.get("residential_likely"):
                logger.info(
                    "[proxy] using DIRECT residential egress ip=%s org=%s",
                    direct.get("ip"),
                    direct.get("org"),
                )
                return None, "direct"
            logger.info(
                "[proxy] direct egress not residential (ip=%s org=%s) — skipping",
                direct.get("ip"),
                direct.get("org"),
            )
        except Exception as exc:
            logger.warning("[proxy] direct egress check failed: %s", exc)

    if require:
        raise RuntimeError(
            "No healthy residential egress available. "
            "Tried: env SOCKS, APE/Warren, office tunnels "
            f"({LOCAL_SOCKS}, {DEFAULT_SOCKS}), and direct residential. "
            "Fix: (1) disconnect VPN on the office Mac (home ISP only); "
            "(2) keep Mac Warren node running "
            "(`launchctl print gui/$(id -u)/com.warren.node` → state=running) "
            "and WARREN_* env on the VPS scraper host; "
            "(3) or set SCRAPER_SOCKS_PROXY to a working US residential SOCKS endpoint."
        )
    return None, "none"
