"""
Tennessee TnCIS statewide case inquiry adapter — FAIL CLOSED.

Portal: https://lgc-tn.com/tncis-web-inquiry/ (LGC / TnCIS Web Inquiry, Cloudflare)

Owner decision 2026-09-25 (Brendan / CoS): TnCIS has no proven public source
contract, and ordinary public access is answered by a Cloudflare interstitial.
This scope therefore fails closed:

* ``SOURCE_CONTRACT_VALIDATED = False`` — ``BaseScraper.run()`` stops before
  any source request, scoring, persistence, or alert.
* ``scrape()`` makes at most ONE ordinary direct request (no proxy, no TLS
  impersonation, no stealth browser, no Obscura) and raises
  :class:`AntiBotBlocked` (error class ``anti_bot``, never retried) the moment
  a Cloudflare / anti-bot answer is seen.
* The former fallback chain (curl_cffi + residential proxy, curl_cffi + mobile
  proxy, Patchright stealth, Obscura CDP) was removed. It is a WAF bypass and
  is not permitted. ``_get_obscura_browser`` is also hard-refused by
  ``BaseScraper._obscura_guard`` because the scope is ``fail_closed`` and on
  ``OBSCURA_HARD_DENY_LABELS``.

Reopen only with a documented, scope-specific public source contract
(ordinary access, source-issued booking/inmate identifier — a court case
number is not a booking key). See docs/ops/SCRAPER_SELF_HEALING.md.
"""

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, List

from scrapers.base_scraper import BaseScraper
from scrapers.scraper_resilience import AntiBotBlocked
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
BASE_URL = "https://lgc-tn.com/tncis-web-inquiry/"

TNCIS_FAIL_CLOSED_REASON = (
    "TnCIS (TN) fail closed: portal is Cloudflare-protected and has no proven public "
    "source contract (no verified source booking key). No Obscura/proxy/stealth "
    "fallback is permitted. Reopen only with a documented public contract."
)

# Markers of a Cloudflare / anti-bot answer to an ordinary request.
_ANTI_BOT_BODY_MARKERS = (
    "just a moment",
    "cf-chl",
    "challenge-platform",
    "attention required",
    "cf-turnstile",
    "captcha",
    "access denied",
)


def is_anti_bot_response(status_code: int, headers: Any, body: str) -> bool:
    """True when an ordinary response is a WAF / Cloudflare / CAPTCHA answer."""
    headers = {str(k).lower(): str(v) for k, v in dict(headers or {}).items()}
    server = headers.get("server", "").lower()
    if headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    if status_code in (401, 403, 429, 503) and ("cloudflare" in server or "cf-ray" in headers):
        return True
    if status_code in (401, 403, 429):
        return True
    text = (body or "")[:20000].lower()
    return any(marker in text for marker in _ANTI_BOT_BODY_MARKERS)


class TennesseeTnCISScraperV2APE(BaseScraper):
    """
    Tennessee TnCIS statewide adapter. Fail closed (see module docstring).

    The class name is kept for import compatibility; the Autonomous Proxy
    Engine / stealth fallback chain it once used has been removed.
    """

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = TNCIS_FAIL_CLOSED_REASON

    @property
    def county(self) -> str:
        return "Tennessee_Statewide"

    def scrape(self) -> List[ArrestRecord]:
        """Fail closed. Never falls back to Obscura, proxies, or stealth browsers.

        ``run()`` never reaches this while ``SOURCE_CONTRACT_VALIDATED`` is
        False. If called directly, it refuses outright while the contract is
        unverified; if a future contract is proven, it makes a single ordinary
        direct request and raises :class:`AntiBotBlocked` on any Cloudflare /
        anti-bot answer (no retry, no alternate route).
        """
        if not getattr(self, "SOURCE_CONTRACT_VALIDATED", False):
            raise AntiBotBlocked(TNCIS_FAIL_CLOSED_REASON)

        import requests
        from bs4 import BeautifulSoup

        session = requests.Session()
        session.trust_env = False  # never inherit HTTP(S)_PROXY / SOCKS from env
        try:
            resp = session.get(BASE_URL, timeout=20, allow_redirects=True)
        finally:
            session.close()
        if is_anti_bot_response(resp.status_code, resp.headers, resp.text):
            raise AntiBotBlocked(
                f"TnCIS (TN): Cloudflare/anti-bot answer (HTTP {resp.status_code}); "
                "failing closed — no Obscura/proxy/stealth fallback"
            )
        resp.raise_for_status()
        return self._parse_dom(BeautifulSoup(resp.text, "html.parser"))

    def _parse_dom(self, soup) -> List[ArrestRecord]:
        """Fallback DOM parsing."""
        records = []
        try:
            for row in soup.select("table tr, .case-row, .result-row, .record"):
                cells = row.find_all(["td", "span", "div"])
                if len(cells) < 2:
                    continue
                
                text = row.get_text(" ", strip=True)
                
                name_match = re.search(r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)*,\s*[A-Z][a-z]+)", text)
                if not name_match:
                    continue
                
                full_name = name_match.group(1)
                first_name, middle_name, last_name = self._parse_name(full_name)
                
                case_match = re.search(r"\b(\d{2}-[A-Z]{2}-\d{6})\b", text)
                if not case_match:
                    # Never emit a row without a source-issued identifier.
                    continue
                
                record = ArrestRecord(
                    County="Tennessee",
                    Booking_Number=case_match.group(1),
                    Full_Name=full_name,
                    First_Name=first_name,
                    Middle_Name=middle_name,
                    Last_Name=last_name,
                    Status="In Custody",
                    Facility="Tennessee Court System",
                    LastCheckedMode="INITIAL"
                )
                records.append(record)
        except Exception as e:
            logger.warning(f"TnCIS v2.1 DOM parse error: {e}")
        
        return records
    
    @staticmethod
    def _get_field(entry: dict, keys: List[str]) -> str:
        """Get field value from dict using multiple possible keys."""
        for key in keys:
            if key in entry and entry[key]:
                val = entry[key]
                if isinstance(val, list):
                    return " | ".join(str(v) for v in val if v)
                return str(val).strip()
        return ""
    
    @staticmethod
    def _parse_name(name_str: str):
        """Parse name string into first, middle, last."""
        if not name_str:
            return "", "", ""
        
        if "," in name_str:
            parts = name_str.split(",", 1)
            last_name = parts[0].strip()
            first_middle = parts[1].strip() if len(parts) > 1 else ""
            name_parts = first_middle.split()
            first_name = name_parts[0] if name_parts else ""
            middle_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
            return first_name, middle_name, last_name
        
        parts = name_str.split()
        return parts[0], "", parts[-1] if len(parts) >= 2 else ""
    
    @staticmethod
    def _get_date_range_start() -> str:
        """Get start date for case search (last 7 days)."""
        start = datetime.now(timezone.utc) - timedelta(days=7)
        return start.strftime("%m/%d/%Y")
