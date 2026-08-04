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
    """Ordered list of office / Tailscale SOCKS URLs to try (unique, non-empty)."""
    candidates: List[str] = [
        os.environ.get("SCRAPER_SOCKS_PROXY") or "",
        os.environ.get("SOCKS_PROXY") or "",
    ]
    # Tailscale mesh → iMac residential SOCKS (replaces fragile ssh -R when up)
    try:
        from config.tailscale import ts_config

        if ts_config.enabled:
            candidates.append(ts_config.imac_socks_url)
            # Also try raw 100.x IP if hostname differs
            if ts_config.imac_ip:
                candidates.append(f"socks5://{ts_config.imac_ip}:{ts_config.socks_port}")
    except Exception:
        pass
    candidates.extend([LOCAL_SOCKS, DEFAULT_SOCKS])

    seen = set()
    out: List[str] = []
    for c in candidates:
        c = c.strip()
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def _is_socks_url(proxy_url: str) -> bool:
    return (proxy_url or "").lower().startswith(("socks5://", "socks5h://", "socks4://"))


def validate_residential_proxy(
    proxy_url: Optional[str],
    *,
    require_residential_exit: bool = True,
    timeout: float = 15.0,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Health-check a proxy and optionally verify US residential exit IP.

    Returns ``(ok, exit_info)``. For SOCKS, CONNECT must succeed. For HTTP
    Warren hubs, TCP endpoint + exit-IP org must look residential.
    """
    info: Dict[str, Any] = {}
    if proxy_url is None:
        # Direct host egress
        if not require_residential_exit:
            return True, info
        try:
            from scrapers.cf_browser import check_exit_ip

            info = check_exit_ip(None, timeout=timeout, retries=2)
            return bool(info.get("residential_likely")), info
        except Exception as exc:
            return False, {"error": str(exc)}

    normalized = _normalize_playwright_proxy(proxy_url)
    if _is_socks_url(normalized):
        if not socks5_connect_ok(normalized, timeout=min(4.0, timeout)):
            return False, {"error": "socks_connect_failed"}
        if not require_residential_exit:
            return True, info
        try:
            from scrapers.cf_browser import check_exit_ip

            info = check_exit_ip(normalized, timeout=timeout, retries=2)
            return bool(info.get("residential_likely")), info
        except Exception as exc:
            # CONNECT worked; treat as usable if exit check is flaky
            logger.warning(
                "[proxy] SOCKS exit check failed after CONNECT ok: %s — accepting tunnel",
                exc,
            )
            return True, {"error": str(exc), "socks_connect_ok": True}

    # HTTP(S) proxy (Warren hub)
    if not http_proxy_endpoint_ok(normalized, timeout=min(4.0, timeout)):
        return False, {"error": "http_endpoint_unreachable"}
    if not require_residential_exit:
        return True, info
    try:
        from scrapers.cf_browser import check_exit_ip

        info = check_exit_ip(normalized, timeout=timeout, retries=2)
        return bool(info.get("residential_likely")), info
    except Exception as exc:
        return False, {"error": str(exc)}


def resolve_residential_proxy(
    scraper: Any = None,
    *,
    prefer_residential: bool = True,
    require: bool = True,
    sticky_session: Optional[str] = None,
    max_ape_attempts: int = 5,
) -> Tuple[Optional[str], str]:
    """Resolve a proxy for Cloudflare / AWS-WAF-protected scrapers.

    Never silently returns a Hetzner/datacenter path. Order:

      1. Env SOCKS (ops pin) — CONNECT + residential exit
      2. APE residential-only pool (Tailscale → Warren → S5W2C), multi-try
      3. Office / Tailscale SOCKS candidates (CONNECT + exit when possible)
      4. Direct only if *this host* is already US residential (office Mac)

    Stormsia free lists are **excluded** (datacenter). VPS direct is **excluded**.

    Args:
        scraper: Optional BaseScraper instance (uses ``get_proxy`` / APE metrics).
        prefer_residential: Passed to APE (always True effectively for this path).
        require: If True, raise RuntimeError when nothing healthy is available.
        sticky_session: Prefer APE sticky routing for multi-page CF flows.
        max_ape_attempts: How many APE candidates to try before falling back.

    Returns:
        ``(proxy_url, source)`` where source is
        ``env_socks`` | ``ape`` | ``office_socks`` | ``tailscale_socks`` | ``direct`` | ``none``.
    """
    prefer_residential = True  # this resolver is residential-only by design
    _ = prefer_residential

    # 1) Explicit env SOCKS — ops pin
    env_url = (
        os.environ.get("SCRAPER_SOCKS_PROXY") or os.environ.get("SOCKS_PROXY") or ""
    ).strip()
    if env_url:
        ok, info = validate_residential_proxy(env_url, require_residential_exit=True)
        if ok:
            logger.info(
                "[proxy] using env SOCKS (%s) ip=%s org=%s",
                env_url.split("@")[-1],
                info.get("ip"),
                info.get("org"),
            )
            return _normalize_playwright_proxy(env_url), "env_socks"
        logger.warning(
            "[proxy] env SOCKS rejected (%s): %s",
            env_url.split("@")[-1],
            info.get("error") or info,
        )

    # 2) APE residential-only multi-try (Warren sticky + pool)
    tried: set = set()
    if scraper is not None:
        for attempt in range(max(1, max_ape_attempts)):
            ape_proxy = None
            try:
                if sticky_session and hasattr(scraper, "get_sticky_proxy"):
                    sid = sticky_session if attempt == 0 else f"{sticky_session}-r{attempt}"
                    ape_proxy = scraper.get_sticky_proxy(
                        sid, residential_only=True
                    )
                if not ape_proxy and hasattr(scraper, "get_proxy"):
                    ape_proxy = scraper.get_proxy(
                        prefer_residential=True, residential_only=True
                    )
                # Prefer APE engine multi-validate when present
                if (
                    not ape_proxy
                    and getattr(scraper, "ape", None) is not None
                    and hasattr(scraper.ape, "get_validated_residential_proxy")
                ):
                    ape_proxy = scraper.ape.get_validated_residential_proxy(
                        sticky_session_id=sticky_session,
                        max_attempts=1,
                        validate=lambda u: u not in tried,
                    )
            except TypeError:
                # Older get_sticky_proxy / get_proxy without residential_only kw
                try:
                    if sticky_session and hasattr(scraper, "get_sticky_proxy"):
                        ape_proxy = scraper.get_sticky_proxy(sticky_session)
                    if not ape_proxy and hasattr(scraper, "get_proxy"):
                        ape_proxy = scraper.get_proxy(prefer_residential=True)
                except Exception as exc:
                    logger.warning("[proxy] APE get_proxy failed: %s", exc)
                    ape_proxy = None
            except Exception as exc:
                logger.warning("[proxy] APE get_proxy failed: %s", exc)
                ape_proxy = None

            if not ape_proxy or ape_proxy in tried:
                continue
            tried.add(ape_proxy)
            normalized = _normalize_playwright_proxy(ape_proxy)
            ok, info = validate_residential_proxy(
                normalized, require_residential_exit=True
            )
            if ok:
                logger.info(
                    "[proxy] using APE residential ip=%s org=%s (attempt %d)",
                    info.get("ip"),
                    info.get("org"),
                    attempt + 1,
                )
                return normalized, "ape"
            logger.warning(
                "[proxy] APE candidate rejected (ip=%s org=%s err=%s) — rotating",
                info.get("ip"),
                info.get("org"),
                info.get("error"),
            )
            if hasattr(scraper, "record_proxy_failure"):
                try:
                    scraper.record_proxy_failure(ape_proxy)
                except Exception:
                    pass

    # 3) Office / Tailscale SOCKS candidates
    for candidate in _office_socks_candidates():
        if candidate in tried:
            continue
        ok, info = validate_residential_proxy(
            candidate, require_residential_exit=True
        )
        if not ok:
            logger.debug(
                "[proxy] SOCKS candidate rejected (%s): %s",
                candidate.split("@")[-1],
                info.get("error") or info,
            )
            continue
        source = (
            "tailscale_socks"
            if ("100." in candidate or "shamrock" in candidate.lower())
            else "office_socks"
        )
        logger.info(
            "[proxy] using %s (%s) ip=%s org=%s",
            source,
            candidate.split("@")[-1],
            info.get("ip"),
            info.get("org"),
        )
        return _normalize_playwright_proxy(candidate), source

    # 4) Direct only when host itself is US residential (never Hetzner VPS)
    allow_direct = os.environ.get("SCRAPER_ALLOW_DIRECT", "true").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if allow_direct:
        ok, info = validate_residential_proxy(None, require_residential_exit=True)
        if ok:
            logger.info(
                "[proxy] using DIRECT residential egress ip=%s org=%s",
                info.get("ip"),
                info.get("org"),
            )
            return None, "direct"
        logger.info(
            "[proxy] direct egress not residential (ip=%s org=%s) — refusing VPS IP",
            info.get("ip"),
            info.get("org"),
        )

    if require:
        raise RuntimeError(
            "No healthy residential egress available for WAF/CF scrapers. "
            "Tried: env SOCKS, APE/Warren (residential-only, multi-try), "
            "Tailscale/office SOCKS, and direct residential. "
            "VPS datacenter IP is intentionally blocked. "
            "Fix: (1) ensure Warren hub + mac-office node online "
            "(WARREN_* env on VPS; `com.warren.node` on iMac); "
            "(2) restore iMac SOCKS — Tailscale :1080 or ssh -R to VPS :1080 "
            "with a working CONNECT (not a stale listen); "
            "(3) or set SCRAPER_SOCKS_PROXY to a US residential SOCKS endpoint."
        )
    return None, "none"


def curl_cffi_proxies(proxy_url: Optional[str]) -> Optional[Dict[str, str]]:
    """Build curl_cffi ``proxies=`` dict from a residential proxy URL."""
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}
