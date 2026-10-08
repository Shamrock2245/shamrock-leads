"""
Exit-IP preflight for residential-only scrapers.

:func:`check_exit_ip` reports a host's (or proxy's) public exit IP and whether
it looks like a US residential ISP. An exit whose org/country can't be looked
up is ``exit_unverified`` and never treated as residential (#113).

The Patchright stealth launcher (``launch_cf_browser`` / ``new_stealth_context``
/ ``wait_past_cloudflare``) and ``require_residential_exit`` were removed
2026-10-07: their last user, Charlotte, now runs like Manatee (stock Playwright,
no proxy, Leads Ops relay only). The shared proxy resolver in
:mod:`scrapers.socks_proxy` stays for Marion and Hillsborough.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

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


def check_exit_ip(
    proxy_url: Optional[str],
    *,
    timeout: float = 20.0,
    retries: int = 3,
    trust_env: bool = True,
) -> Dict[str, Any]:
    """Return public exit IP metadata via the proxy (or direct if proxy_url is None).

    ``trust_env=False`` ignores HTTP(S)_PROXY / ALL_PROXY so a direct check
    really measures this host's own exit (Manatee).

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
                "trust_env": trust_env,
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
            # Fail closed when the org/country lookup came back empty (e.g. the
            # IP-info APIs rate-limited us): an unknown exit is not proof of a
            # residential one. 2026-10-07: a datacenter box passed this check
            # with org="" / country="" while ipinfo/ipapi answered 429.
            known = bool(org_l.strip()) and bool(country)
            us_ok = country in ("US", "USA")
            info["exit_unverified"] = info["ok"] and not known
            info["residential_likely"] = info["ok"] and known and not dc_hit and us_ok
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
