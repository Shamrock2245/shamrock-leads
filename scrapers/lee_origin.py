"""
Lee County Sheriff (FL) origin routing.

Background
----------
``www.sheriffleefl.org`` is the canonical public hostname, but its DNS A-record
can point at a dead origin (connection refused on :443) while the apex
``sheriffleefl.org`` host still serves the same nginx/app and answers the
``/public-api/*`` JSON endpoints when requested with Host/SNI ``www``.

Browsers follow apex → www 301 and hang. Scrapers that resolve ``www`` via
normal DNS also hang. Fix: pin ``www`` to a working origin IP via
``CURLOPT_RESOLVE`` (curl_cffi) so Host/SNI/TLS stay correct.

This module is intentionally lightweight so the FirstAppearanceWatcher and
URL-ingest paths can share the same pin without importing the full Lee scraper.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

LEE_HOST = "www.sheriffleefl.org"
LEE_BASE_URL = f"https://{LEE_HOST}"
LEE_APEX_HOST = "sheriffleefl.org"
BOOKINGS_PATH = "/public-api/bookings"

# Optional permanent override (ops / docker-compose). Prefer auto-discovery.
_ENV_ORIGIN_IP = (os.getenv("LEE_ORIGIN_IP") or "").strip()

# Known-good apex origin (sheriffleefl.org). www A-record often points at a
# dead host (conn-refused). Keep this list updated if FDOT/LCSO relocates.
# Probe may return 429 when rate-limited — that still means "IP is alive".
_KNOWN_GOOD_ORIGIN_IPS: Sequence[str] = tuple(
    ip.strip()
    for ip in (os.getenv("LEE_KNOWN_ORIGIN_IPS") or "139.177.205.107").split(",")
    if ip.strip()
)

# Cache discovery so every charges call does not re-probe.
_cache_lock = threading.Lock()
_cached_ip: Optional[str] = None
_cached_at: float = 0.0
_CACHE_TTL_S = float(os.getenv("LEE_ORIGIN_CACHE_TTL_S", "300"))


def _dns_ips(hostname: str) -> List[str]:
    ips: List[str] = []
    try:
        for info in socket.getaddrinfo(hostname, 443, socket.AF_INET, socket.SOCK_STREAM):
            ip = info[4][0]
            if ip and ip not in ips:
                ips.append(ip)
    except OSError as exc:
        logger.debug("Lee origin DNS failed for %s: %s", hostname, exc)
    return ips


def candidate_origin_ips() -> List[str]:
    """Ordered IPs to try: env override, known-good apex, DNS apex, then www (often dead)."""
    ordered: List[str] = []
    if _ENV_ORIGIN_IP:
        ordered.append(_ENV_ORIGIN_IP)
    for ip in _KNOWN_GOOD_ORIGIN_IPS:
        if ip not in ordered:
            ordered.append(ip)
    for host in (LEE_APEX_HOST, LEE_HOST):
        for ip in _dns_ips(host):
            if ip not in ordered:
                ordered.append(ip)
    return ordered


def _probe_ip(ip: str, timeout: float = 10.0) -> bool:
    """
    Return True if this IP answers TLS for Host www on the public API.

    HTTP 200 (JSON) is ideal. HTTP 429 means the origin is *alive* but our
    source IP is rate-limited — still a valid pin target (use residential
    egress to actually fetch data).

    On 429 we trip the shared cooldown once, then stop probing further IPs.
    """
    try:
        from curl_cffi import CurlOpt
        from curl_cffi import requests as cffi_requests
    except ImportError:
        return False

    # Already throttled — do not spend more probe requests against the quota.
    try:
        from scrapers.lee_rate_limit import is_cooled_down

        if is_cooled_down():
            return True  # treat as reachable; caller will use pin + residential
    except Exception:
        pass

    url = f"{LEE_BASE_URL}{BOOKINGS_PATH}?limit=1&offset=0"
    try:
        resp = cffi_requests.get(
            url,
            headers={
                "Accept": "application/json, text/html, */*;q=0.8",
                "Referer": f"{LEE_BASE_URL}/",
            },
            impersonate="chrome131",
            timeout=timeout,
            curl_options={CurlOpt.RESOLVE: [f"{LEE_HOST}:443:{ip}"]},
        )
        code = resp.status_code
        if code == 429:
            try:
                from scrapers.lee_rate_limit import note_response

                note_response(resp)
            except Exception:
                pass
            logger.info(
                "[Lee origin] probe %s → HTTP 429 (origin alive, source IP throttled)",
                ip,
            )
            return True
        if code != 200:
            return False
        # Prefer JSON body; empty list is still a valid healthy API.
        ctype = (resp.headers.get("content-type") or "").lower()
        if "json" in ctype:
            return True
        text = (resp.text or "").lstrip()
        return text.startswith("[") or text.startswith("{")
    except Exception as exc:
        logger.debug("Lee origin probe %s failed: %s", ip, exc)
        return False


def discover_lee_origin_ip(
    *, force: bool = False, timeout: float = 10.0
) -> Optional[str]:
    """
    Find a working origin IP for ``www.sheriffleefl.org``.

    Result is cached for ``LEE_ORIGIN_CACHE_TTL_S`` (default 5 min).
    Falls back to known-good apex IPs even if live probe is inconclusive
    (e.g. mid-cooldown) so CURLOPT_RESOLVE still bypasses the dead www A-record.
    """
    global _cached_ip, _cached_at

    with _cache_lock:
        if (
            not force
            and _cached_ip
            and (time.time() - _cached_at) < _CACHE_TTL_S
        ):
            return _cached_ip

    candidates = candidate_origin_ips()

    # If we're already rate-limited, skip live probes entirely — pin known-good.
    try:
        from scrapers.lee_rate_limit import is_cooled_down

        if is_cooled_down():
            fallback = next(iter(_KNOWN_GOOD_ORIGIN_IPS), None) or (
                candidates[0] if candidates else None
            )
            if fallback:
                with _cache_lock:
                    _cached_ip = fallback
                    _cached_at = time.time()
                return fallback
    except Exception:
        pass

    for ip in candidates:
        if _probe_ip(ip, timeout=timeout):
            with _cache_lock:
                _cached_ip = ip
                _cached_at = time.time()
            logger.info(
                "[Lee origin] pinned %s → %s (www DNS may point at dead host)",
                LEE_HOST,
                ip,
            )
            return ip

    # Last resort: pin known-good apex even without a successful probe so we
    # never fall through to the dead www A-record (conn-refused).
    fallback = next(iter(_KNOWN_GOOD_ORIGIN_IPS), None) or (
        candidates[0] if candidates else None
    )
    if fallback:
        with _cache_lock:
            _cached_ip = fallback
            _cached_at = time.time()
        logger.warning(
            "[Lee origin] probe inconclusive — using fallback pin %s → %s",
            LEE_HOST,
            fallback,
        )
        return fallback

    logger.warning(
        "[Lee origin] no working origin found for %s (candidates=%s)",
        LEE_HOST,
        candidates,
    )
    with _cache_lock:
        # Negative cache briefly so we don't stampede probes.
        _cached_ip = None
        _cached_at = time.time()
    return None


def lee_resolve_entries(ip: Optional[str] = None) -> List[str]:
    """CURLOPT_RESOLVE entries: ``host:port:address``."""
    pinned = ip or discover_lee_origin_ip()
    if not pinned:
        # Absolute last resort hardcode so callers never hit dead www DNS.
        pinned = next(iter(_KNOWN_GOOD_ORIGIN_IPS), None)
    if not pinned:
        return []
    return [f"{LEE_HOST}:443:{pinned}"]


def lee_curl_options(ip: Optional[str] = None) -> Dict[Any, Any]:
    """curl_cffi Session ``curl_options`` map with RESOLVE pin when available."""
    try:
        from curl_cffi import CurlOpt
    except ImportError:
        return {}
    entries = lee_resolve_entries(ip)
    if not entries:
        return {}
    return {CurlOpt.RESOLVE: entries}


def lee_api_get(
    path_or_url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
    impersonate: str = "chrome131",
    proxies: Optional[Dict[str, str]] = None,
    max_retries: int = 2,
) -> Any:
    """
    GET a Lee Sheriff URL with DNS pin + Chrome JA3.

    ``path_or_url`` may be absolute or a path under the Lee base URL.
    Returns a curl_cffi Response (or raises after retries).
    """
    from curl_cffi import CurlOpt
    from curl_cffi import requests as cffi_requests

    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        url = path_or_url
    else:
        url = f"{LEE_BASE_URL}{path_or_url if path_or_url.startswith('/') else '/' + path_or_url}"

    if params:
        from urllib.parse import urlencode

        qs = urlencode(params)
        url = f"{url}{'&' if '?' in url else '?'}{qs}"

    req_headers = {
        "Accept": "application/json, text/html, */*;q=0.8",
        "Referer": f"{LEE_BASE_URL}/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    if headers:
        req_headers.update(headers)

    # Shared 429 cooldown — do not dig the hole deeper
    try:
        from scrapers.lee_rate_limit import is_cooled_down, note_response, seconds_remaining

        if is_cooled_down():
            raise RuntimeError(
                f"Lee public-api rate-limit cooldown active "
                f"({seconds_remaining():.0f}s remaining)"
            )
    except ImportError:
        note_response = None  # type: ignore

    last_exc: Optional[BaseException] = None
    for attempt in range(max(1, max_retries)):
        # Re-discover on later attempts in case the pin went stale.
        force = attempt > 0
        resolve = lee_resolve_entries(
            discover_lee_origin_ip(force=force) if force else None
        )
        curl_opts = {CurlOpt.RESOLVE: resolve} if resolve else {}
        try:
            session = cffi_requests.Session(curl_options=curl_opts or None)
            try:
                resp = session.get(
                    url,
                    headers=req_headers,
                    impersonate=impersonate,
                    timeout=timeout,
                    proxies=proxies,
                )
            finally:
                try:
                    session.close()
                except Exception:
                    pass
            if note_response is not None:
                try:
                    note_response(resp)
                except Exception:
                    pass
            return resp
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "[Lee origin] GET attempt %d failed: %s", attempt + 1, exc
            )
            time.sleep(1.5 * (attempt + 1))

    if last_exc:
        raise last_exc
    raise RuntimeError(f"Lee origin GET failed for {url}")


def invalidate_lee_origin_cache() -> None:
    """Force the next caller to re-probe (e.g. after prolonged  connection errors)."""
    global _cached_ip, _cached_at
    with _cache_lock:
        _cached_ip = None
        _cached_at = 0.0
