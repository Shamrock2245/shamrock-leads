"""
Cloudflare-aware browser launch helpers for Revize / WAF scrapers.

Uses Patchright (stealth Chromium) when installed, else Playwright.
Always pairs with residential egress from :mod:`scrapers.socks_proxy`.

Preflight: verifies the proxy's public exit IP is not a known datacenter/VPN ASN
(Cloudflare will never clear for Datacamp/DO/AWS egress).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Orgs/ASNs that CF typically hard-blocks (partial match, case-insensitive)
_DATACENTER_MARKERS = (
    "datacamp",
    "digitalocean",
    "amazon",
    "aws",
    "google cloud",
    "microsoft",
    "azure",
    "linode",
    "akamai",
    "ovh",
    "hetzner",
    "vultr",
    "contabo",
    "m247",
    "hostinger",
    "choopa",
    "psychz",
    "quadranet",
    "serverius",
    "leaseweb",
    "coloCrossing",
    "vpn",
    "proxy",
    "tor-exit",
)


def _launch_sync_playwright():
    """Prefer Patchright stealth; fall back to stock Playwright."""
    try:
        from patchright.sync_api import sync_playwright as sp

        logger.info("[cf_browser] using Patchright")
        return sp, "patchright"
    except ImportError:
        from playwright.sync_api import sync_playwright as sp

        logger.info("[cf_browser] using Playwright (install patchright for better CF bypass)")
        return sp, "playwright"


def check_exit_ip(
    proxy_url: Optional[str],
    *,
    timeout: float = 20.0,
    retries: int = 3,
) -> Dict[str, Any]:
    """Return public exit IP metadata via the proxy (or direct if proxy_url is None).

    Keys: ok, ip, org, country, city, residential_likely, raw
    """
    import httpx

    info: Dict[str, Any] = {
        "ok": False,
        "ip": None,
        "org": None,
        "country": None,
        "city": None,
        "residential_likely": False,
        "raw": {},
    }
    last_err: Optional[str] = None
    endpoints = (
        "https://ipinfo.io/json",
        "https://api.ipify.org?format=json",
    )

    for attempt in range(1, retries + 1):
        try:
            client_kwargs: Dict[str, Any] = {
                "timeout": timeout,
                "follow_redirects": True,
            }
            if proxy_url:
                client_kwargs["proxy"] = proxy_url
            with httpx.Client(**client_kwargs) as client:
                data = None
                for url in endpoints:
                    try:
                        r = client.get(url)
                        r.raise_for_status()
                        payload = r.json()
                        if "ip" not in payload and "origin" in payload:
                            payload = {"ip": str(payload["origin"]).split(",")[0].strip()}
                        # Enrich bare ipify with org lookup when needed
                        if payload.get("ip") and not payload.get("org"):
                            try:
                                r2 = client.get(f"https://ipapi.co/{payload['ip']}/json/")
                                if r2.status_code == 200:
                                    extra = r2.json()
                                    payload.setdefault("org", extra.get("org") or extra.get("asn"))
                                    payload.setdefault("country", extra.get("country_code") or extra.get("country"))
                                    payload.setdefault("city", extra.get("city"))
                            except Exception:
                                pass
                        data = payload
                        break
                    except Exception as e:
                        last_err = str(e)
                        continue
                if not data:
                    raise RuntimeError(last_err or "all IP endpoints failed")

            info["raw"] = data if isinstance(data, dict) else {}
            info["ip"] = data.get("ip")
            info["org"] = data.get("org") or data.get("org_name") or data.get("asn") or ""
            info["country"] = data.get("country") or data.get("country_code") or ""
            info["city"] = data.get("city") or ""
            info["ok"] = bool(info["ip"])

            org_l = str(info["org"]).lower()
            dc_hit = any(m in org_l for m in _DATACENTER_MARKERS)
            country = str(info["country"] or "").upper()
            us_ok = country in ("", "US", "USA")
            info["residential_likely"] = info["ok"] and not dc_hit and us_ok
            return info
        except Exception as e:
            last_err = str(e)
            logger.warning(
                "[cf_browser] exit IP check attempt %d/%d failed: %s",
                attempt,
                retries,
                e,
            )
            continue

    info["error"] = last_err or "exit IP check failed"
    return info


def require_residential_exit(
    proxy_url: Optional[str],
    *,
    label: str = "scraper",
) -> Dict[str, Any]:
    """Raise RuntimeError if exit looks like datacenter/VPN (CF will never pass).

    ``proxy_url`` None = direct host egress (office Mac on home ISP).
    """
    info = check_exit_ip(proxy_url)
    if not info.get("ok"):
        raise RuntimeError(
            f"[{label}] could not verify exit IP "
            f"({info.get('error') or 'unknown'}). "
            "Check WARREN_* credentials and that mac-office Warren node is online, "
            "or run on a US residential network (no VPN)."
        )
    if info.get("residential_likely"):
        logger.info(
            "[%s] residential exit OK ip=%s org=%s country=%s via=%s",
            label,
            info.get("ip"),
            info.get("org"),
            info.get("country"),
            "proxy" if proxy_url else "direct",
        )
        return info

    raise RuntimeError(
        f"[{label}] exit is NOT usable residential US for Cloudflare. "
        f"ip={info.get('ip')} country={info.get('country')} org={info.get('org')!r}. "
        "Cloudflare will stay on 'Just a moment…' forever. "
        "Fix: (1) disconnect VPN on the office Mac so egress is a true home ISP IP; "
        "(2) confirm `com.warren.node` (mac-office) is running on that Mac; "
        "(3) or set SCRAPER_SOCKS_PROXY to a US residential SOCKS endpoint. "
        "Datacamp/DO/AWS/Bahamas exits will not pass Revize CF."
    )


def launch_cf_browser(
    proxy_url: Optional[str],
    *,
    label: str = "scraper",
    verify_residential: bool = True,
    headless: bool = True,
) -> Tuple[Any, Any, str]:
    """Launch stealth browser with optional proxy.

    ``proxy_url`` may be None for direct residential egress (office Mac on home ISP).

    Returns:
        (playwright_instance, browser, engine_name)

    Caller must close browser and stop playwright in finally.
    """
    from scrapers.socks_proxy import to_playwright_proxy

    if verify_residential:
        require_residential_exit(proxy_url, label=label)

    pw_proxy = to_playwright_proxy(proxy_url) if proxy_url else None
    if pw_proxy:
        logger.info(
            "[%s] launching browser server=%s user=%s",
            label,
            pw_proxy.get("server"),
            pw_proxy.get("username") or "(none)",
        )
    else:
        logger.info("[%s] launching browser with DIRECT residential egress (no proxy)", label)

    sync_playwright, engine = _launch_sync_playwright()
    pw = sync_playwright().start()

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-sandbox",
    ]

    # Prefer system Chrome when available (better CF behavior than stock chromium)
    browser = None
    last_err = None
    for channel in ("chrome", None):
        try:
            kwargs: Dict[str, Any] = {
                "headless": headless,
                "args": launch_args,
            }
            if pw_proxy:
                kwargs["proxy"] = pw_proxy
            if channel:
                kwargs["channel"] = channel
            browser = pw.chromium.launch(**kwargs)
            logger.info("[%s] browser engine=%s channel=%s", label, engine, channel or "chromium")
            break
        except Exception as e:
            last_err = e
            logger.debug("[%s] launch channel=%s failed: %s", label, channel, e)
            continue

    if browser is None:
        pw.stop()
        raise RuntimeError(f"[{label}] failed to launch browser: {last_err}")

    return pw, browser, engine


def new_stealth_context(browser, *, user_agent: Optional[str] = None):
    """Create a browser context with basic anti-automation patches."""
    ua = user_agent or (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    context = browser.new_context(
        user_agent=ua,
        viewport={"width": 1440, "height": 900},
        locale="en-US",
        timezone_id="America/New_York",
    )
    context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        window.chrome = { runtime: {} };
        """
    )
    return context


def wait_past_cloudflare(page, label: str = "", max_wait: int = 45) -> bool:
    """Wait until CF interstitial clears and a table appears (when present)."""
    import time

    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            title = (page.title() or "").lower()
        except Exception:
            time.sleep(1.5)
            continue

        blocked = any(
            x in title
            for x in ("just a moment", "attention", "blocked", "security verification")
        )
        if not blocked:
            try:
                if page.query_selector("table tbody tr"):
                    return True
            except Exception:
                pass
            try:
                if page.query_selector("table"):
                    return True
            except Exception:
                pass
            # Title clear — accept even without table (some pages use dropdowns)
            return True
        time.sleep(1.5)

    logger.error("[%s] Cloudflare still blocked after %ss", label or "page", max_wait)
    return False
