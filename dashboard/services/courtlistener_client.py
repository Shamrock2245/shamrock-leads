"""
CourtListener REST API Client — ShamrockLeads Intelligence Suite
================================================================

Interfaces with CourtListener.com v4 API to pull court opinions,
dockets, opinion clusters, judges, and citations across Shamrock's
multi-state footprint (FL, GA, AL, MS, LA, TN, KY, NC, SC, VA, WV, AR,
TX, CT, OH).

Data feeds into `court_outcomes` MongoDB collection for empirical
FTA/conviction modeling, bail precedent analytics, and the Court
Outcome Predictor.

See: https://www.courtlistener.com/api/rest/v4/
     https://wiki.free.law/c/courtlistener/help/api/rest/v4/case-law
"""

import asyncio
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

log = logging.getLogger("shamrock.courtlistener")

# ── FLP Maintenance Window (Thu 21:00–23:59 PT) ────────────────────────────
_MAINTENANCE_DOW = 3  # Thursday
_MAINTENANCE_START_HOUR_UTC = 4  # 21:00 PT ≈ 04:00 UTC (next day)
_MAINTENANCE_END_HOUR_UTC = 7   # 23:59 PT ≈ 07:00 UTC

# ── Bail-Relevant Charge Patterns ───────────────────────────────────────────
_BAIL_KEYWORDS = re.compile(
    r"bail|bond|surety|pretrial|detention|release|custody|remand"
    r"|arraign|indictment|arrest|warrant|capias"
    r"|felony|misdemeanor|criminal|probation|parole"
    r"|sentenc|convict|acquit|dismiss|plea|guilty|not guilty"
    r"|forfeiture|estreat|revok|fta|failure to appear"
    r"|drug|dui|dwi|assault|battery|theft|burglary|robbery"
    r"|murder|manslaughter|homicide|weapon|firearm",
    re.IGNORECASE,
)

MAX_RETRIES = 3
BACKOFF_BASE = 2.0  # seconds

# ── Southeast US Court Registry ─────────────────────────────────────────────
# CourtListener court IDs mapped to display names
# Source: https://www.courtlistener.com/api/rest/v4/courts/
SE_US_COURTS = {
    # ── Florida ──────────────────────────────────────────────────────────────
    "fla":              {"name": "Florida Supreme Court",           "state": "FL", "type": "state", "tier": "supreme"},
    "fladistctapp_1":   {"name": "Florida 1st DCA",                "state": "FL", "type": "state", "tier": "appellate"},
    "fladistctapp_2":   {"name": "Florida 2nd DCA",                "state": "FL", "type": "state", "tier": "appellate"},
    "fladistctapp_3":   {"name": "Florida 3rd DCA",                "state": "FL", "type": "state", "tier": "appellate"},
    "fladistctapp_4":   {"name": "Florida 4th DCA",                "state": "FL", "type": "state", "tier": "appellate"},
    "fladistctapp_5":   {"name": "Florida 5th DCA",                "state": "FL", "type": "state", "tier": "appellate"},
    "fladistctapp_6":   {"name": "Florida 6th DCA",                "state": "FL", "type": "state", "tier": "appellate"},
    "flsd":             {"name": "S.D. Florida",                   "state": "FL", "type": "federal", "tier": "district"},
    "flmd":             {"name": "M.D. Florida",                   "state": "FL", "type": "federal", "tier": "district"},
    "flnd":             {"name": "N.D. Florida",                   "state": "FL", "type": "federal", "tier": "district"},
    # ── Georgia ──────────────────────────────────────────────────────────────
    "ga":               {"name": "Georgia Supreme Court",          "state": "GA", "type": "state", "tier": "supreme"},
    "gactapp":          {"name": "Georgia Court of Appeals",       "state": "GA", "type": "state", "tier": "appellate"},
    "gand":             {"name": "N.D. Georgia",                   "state": "GA", "type": "federal", "tier": "district"},
    "gamd":             {"name": "M.D. Georgia",                   "state": "GA", "type": "federal", "tier": "district"},
    "gasd":             {"name": "S.D. Georgia",                   "state": "GA", "type": "federal", "tier": "district"},
    # ── Alabama ──────────────────────────────────────────────────────────────
    "ala":              {"name": "Alabama Supreme Court",          "state": "AL", "type": "state", "tier": "supreme"},
    "alacrimapp":       {"name": "Alabama Ct. Criminal Appeals",   "state": "AL", "type": "state", "tier": "appellate"},
    "alacivapp":        {"name": "Alabama Ct. Civil Appeals",      "state": "AL", "type": "state", "tier": "appellate"},
    "alnd":             {"name": "N.D. Alabama",                   "state": "AL", "type": "federal", "tier": "district"},
    "almd":             {"name": "M.D. Alabama",                   "state": "AL", "type": "federal", "tier": "district"},
    "alsd":             {"name": "S.D. Alabama",                   "state": "AL", "type": "federal", "tier": "district"},
    # ── Mississippi ──────────────────────────────────────────────────────────
    "miss":             {"name": "Mississippi Supreme Court",      "state": "MS", "type": "state", "tier": "supreme"},
    "missctapp":        {"name": "Mississippi Ct. of Appeals",     "state": "MS", "type": "state", "tier": "appellate"},
    "msnd":             {"name": "N.D. Mississippi",               "state": "MS", "type": "federal", "tier": "district"},
    "mssd":             {"name": "S.D. Mississippi",               "state": "MS", "type": "federal", "tier": "district"},
    # ── Louisiana ────────────────────────────────────────────────────────────
    "la":               {"name": "Louisiana Supreme Court",        "state": "LA", "type": "state", "tier": "supreme"},
    "lactapp_1":        {"name": "Louisiana 1st Cir. Ct. App.",    "state": "LA", "type": "state", "tier": "appellate"},
    "lactapp_2":        {"name": "Louisiana 2nd Cir. Ct. App.",    "state": "LA", "type": "state", "tier": "appellate"},
    "lactapp_3":        {"name": "Louisiana 3rd Cir. Ct. App.",    "state": "LA", "type": "state", "tier": "appellate"},
    "lactapp_4":        {"name": "Louisiana 4th Cir. Ct. App.",    "state": "LA", "type": "state", "tier": "appellate"},
    "lactapp_5":        {"name": "Louisiana 5th Cir. Ct. App.",    "state": "LA", "type": "state", "tier": "appellate"},
    "laed":             {"name": "E.D. Louisiana",                 "state": "LA", "type": "federal", "tier": "district"},
    "lamd":             {"name": "M.D. Louisiana",                 "state": "LA", "type": "federal", "tier": "district"},
    "lawd":             {"name": "W.D. Louisiana",                 "state": "LA", "type": "federal", "tier": "district"},
    # ── Tennessee ────────────────────────────────────────────────────────────
    "tenn":             {"name": "Tennessee Supreme Court",        "state": "TN", "type": "state", "tier": "supreme"},
    "tennctapp":        {"name": "Tennessee Ct. of Appeals",       "state": "TN", "type": "state", "tier": "appellate"},
    "tenncrimapp":      {"name": "Tennessee Ct. Criminal App.",    "state": "TN", "type": "state", "tier": "appellate"},
    "tned":             {"name": "E.D. Tennessee",                 "state": "TN", "type": "federal", "tier": "district"},
    "tnmd":             {"name": "M.D. Tennessee",                 "state": "TN", "type": "federal", "tier": "district"},
    "tnwd":             {"name": "W.D. Tennessee",                 "state": "TN", "type": "federal", "tier": "district"},
    # ── Kentucky ─────────────────────────────────────────────────────────────
    "ky":               {"name": "Kentucky Supreme Court",         "state": "KY", "type": "state", "tier": "supreme"},
    "kyctapp":          {"name": "Kentucky Ct. of Appeals",        "state": "KY", "type": "state", "tier": "appellate"},
    "kyed":             {"name": "E.D. Kentucky",                  "state": "KY", "type": "federal", "tier": "district"},
    "kywd":             {"name": "W.D. Kentucky",                  "state": "KY", "type": "federal", "tier": "district"},
    # ── North Carolina ───────────────────────────────────────────────────────
    "nc":               {"name": "North Carolina Supreme Court",   "state": "NC", "type": "state", "tier": "supreme"},
    "ncctapp":          {"name": "North Carolina Ct. of Appeals",  "state": "NC", "type": "state", "tier": "appellate"},
    "nced":             {"name": "E.D. North Carolina",            "state": "NC", "type": "federal", "tier": "district"},
    "ncmd":             {"name": "M.D. North Carolina",            "state": "NC", "type": "federal", "tier": "district"},
    "ncwd":             {"name": "W.D. North Carolina",            "state": "NC", "type": "federal", "tier": "district"},
    # ── South Carolina ───────────────────────────────────────────────────────
    "sc":               {"name": "South Carolina Supreme Court",   "state": "SC", "type": "state", "tier": "supreme"},
    "scctapp":          {"name": "South Carolina Ct. of Appeals",  "state": "SC", "type": "state", "tier": "appellate"},
    "scd":              {"name": "D. South Carolina",              "state": "SC", "type": "federal", "tier": "district"},
    # ── Virginia ─────────────────────────────────────────────────────────────
    "va":               {"name": "Virginia Supreme Court",         "state": "VA", "type": "state", "tier": "supreme"},
    "vactapp":          {"name": "Virginia Ct. of Appeals",        "state": "VA", "type": "state", "tier": "appellate"},
    "vaed":             {"name": "E.D. Virginia",                  "state": "VA", "type": "federal", "tier": "district"},
    "vawd":             {"name": "W.D. Virginia",                  "state": "VA", "type": "federal", "tier": "district"},
    # ── West Virginia ────────────────────────────────────────────────────────
    "wva":              {"name": "West Virginia Supreme Court",    "state": "WV", "type": "state", "tier": "supreme"},
    "wvand":            {"name": "N.D. West Virginia",             "state": "WV", "type": "federal", "tier": "district"},
    "wvasd":            {"name": "S.D. West Virginia",             "state": "WV", "type": "federal", "tier": "district"},
    # ── Arkansas ─────────────────────────────────────────────────────────────
    "ark":              {"name": "Arkansas Supreme Court",         "state": "AR", "type": "state", "tier": "supreme"},
    "arkctapp":         {"name": "Arkansas Ct. of Appeals",        "state": "AR", "type": "state", "tier": "appellate"},
    "ared":             {"name": "E.D. Arkansas",                  "state": "AR", "type": "federal", "tier": "district"},
    "arwd":             {"name": "W.D. Arkansas",                  "state": "AR", "type": "federal", "tier": "district"},
    # ── Texas ────────────────────────────────────────────────────────────────
    "tex":              {"name": "Texas Supreme Court",            "state": "TX", "type": "state", "tier": "supreme"},
    "texcrimapp":       {"name": "Texas Court of Criminal Appeals", "state": "TX", "type": "state", "tier": "supreme"},
    "texapp":           {"name": "Texas Courts of Appeals",        "state": "TX", "type": "state", "tier": "appellate"},
    "txnd":             {"name": "N.D. Texas",                     "state": "TX", "type": "federal", "tier": "district"},
    "txsd":             {"name": "S.D. Texas",                     "state": "TX", "type": "federal", "tier": "district"},
    "txed":             {"name": "E.D. Texas",                     "state": "TX", "type": "federal", "tier": "district"},
    "txwd":             {"name": "W.D. Texas",                     "state": "TX", "type": "federal", "tier": "district"},
    # ── Connecticut ──────────────────────────────────────────────────────────
    "conn":             {"name": "Connecticut Supreme Court",      "state": "CT", "type": "state", "tier": "supreme"},
    "connapp":          {"name": "Connecticut Appellate Court",    "state": "CT", "type": "state", "tier": "appellate"},
    "connsuperct":      {"name": "Connecticut Superior Court",     "state": "CT", "type": "state", "tier": "trial"},
    "ctd":              {"name": "D. Connecticut",                 "state": "CT", "type": "federal", "tier": "district"},
    # ── Ohio ─────────────────────────────────────────────────────────────────
    "ohio":             {"name": "Ohio Supreme Court",             "state": "OH", "type": "state", "tier": "supreme"},
    "ohioctapp":        {"name": "Ohio Court of Appeals",          "state": "OH", "type": "state", "tier": "appellate"},
    "ohnd":             {"name": "N.D. Ohio",                      "state": "OH", "type": "federal", "tier": "district"},
    "ohsd":             {"name": "S.D. Ohio",                      "state": "OH", "type": "federal", "tier": "district"},
    # ── Federal Appellate (covering SE/Multi-State circuits) ─────────────────
    "ca2":              {"name": "2nd Circuit (CT,NY,VT)",         "state": "MULTI", "type": "federal", "tier": "circuit"},
    "ca4":              {"name": "4th Circuit (MD,VA,WV,NC,SC)",   "state": "MULTI", "type": "federal", "tier": "circuit"},
    "ca5":              {"name": "5th Circuit (LA,MS,TX)",         "state": "MULTI", "type": "federal", "tier": "circuit"},
    "ca6":              {"name": "6th Circuit (KY,TN,OH,MI)",      "state": "MULTI", "type": "federal", "tier": "circuit"},
    "ca11":             {"name": "11th Circuit (FL,GA,AL)",        "state": "MULTI", "type": "federal", "tier": "circuit"},
}

# Convenience lookups
ALL_COURT_IDS = list(SE_US_COURTS.keys())
SE_US_STATES = [
    "FL", "GA", "AL", "MS", "LA", "TN", "KY", "NC", "SC", "VA", "WV", "AR",
    "TX", "CT", "OH",
]

# Preset bail-intelligence queries exposed in the v4 Explorer UI
BAIL_SEARCH_PRESETS = {
    "bond_forfeiture": {
        "label": "Bond Forfeiture & Estreature",
        "query": '"bond forfeiture" OR estreat OR "set aside forfeiture" OR "bail forfeiture"',
        "type": "o",
    },
    "pretrial_detention": {
        "label": "Pretrial Detention & Bail Hearings",
        "query": '"pretrial detention" OR "Arthur hearing" OR "Nebbia" OR "excessive bail" OR "pretrial release"',
        "type": "o",
    },
    "bond_revocation": {
        "label": "Bond Revocation & Conditions",
        "query": '"revoke bond" OR "bond revocation" OR "motion to revoke" OR "violating conditions of release"',
        "type": "o",
    },
    "fta_remission": {
        "label": "FTA, Remission & Exoneration",
        "query": '"failure to appear" OR remission OR exonerate OR "surety bond"',
        "type": "o",
    },
    "extradition_surety": {
        "label": "Surety Recovery & Extradition",
        "query": 'extradition OR "bail bondsman" OR "bail agent" OR capias',
        "type": "o",
    },
}

BAIL_CRITICAL_DISPOSITIONS = frozenset({
    "bond_forfeiture", "fta", "bond_revoked", "bond_reduced",
    "bond_increased", "probation_violation", "pretrial_order",
})
FAVORABLE_DISPOSITIONS = frozenset({
    "dismissed", "acquittal", "nolle_prosequi", "reversed", "vacated", "remanded",
})
ADVERSE_DISPOSITIONS = frozenset({
    "conviction", "sentencing", "plea", "affirmed", "denied",
})


def classify_disposition_category(disposition: str) -> str:
    """Return broad category for analytics: bail_critical, favorable, adverse, or other."""
    d = (disposition or "").lower()
    if d in BAIL_CRITICAL_DISPOSITIONS:
        return "bail_critical"
    if d in FAVORABLE_DISPOSITIONS:
        return "favorable"
    if d in ADVERSE_DISPOSITIONS:
        return "adverse"
    return "other"


def score_bail_impact(opinion: dict) -> dict:
    """Score how impactful an opinion/docket record is for bail bond intelligence."""
    score = 0
    factors = []
    disp = (opinion.get("disposition") or "unknown").lower()

    bail_critical = {
        "bond_forfeiture": 95,
        "fta": 90,
        "bond_revoked": 85,
        "bond_reduced": 70,
        "bond_increased": 70,
        "probation_violation": 65,
        "pretrial_order": 60,
    }
    if disp in bail_critical:
        score = bail_critical[disp]
        factors.append(f"bail-critical disposition: {disp}")
    elif disp in ("conviction", "sentencing", "plea"):
        score = 50
        factors.append(f"sentencing outcome: {disp}")
    elif disp in ("dismissed", "acquittal", "nolle_prosequi"):
        score = 40
        factors.append(f"favorable outcome: {disp}")
    elif disp != "unknown":
        score = 25
        factors.append(f"general legal outcome: {disp}")

    if opinion.get("matched_defendant_id"):
        score = min(100, score + 20)
        factors.append("matched to existing defendant")

    ai = opinion.get("ai_analysis") or {}
    if isinstance(ai, dict) and ai.get("risk_signal") == "high":
        score = min(100, score + 15)
        factors.append("AI: high risk signal")

    cite_count = int(opinion.get("citation_count") or 0)
    if cite_count >= 10:
        score = min(100, score + 5)
        factors.append(f"cited precedent ({cite_count} cites)")

    return {"score": score, "factors": factors}


def _strip_html_text(raw_html: str) -> str:
    """Convert CourtListener html_with_citations to readable plain text."""
    if not raw_html:
        return ""
    text = re.sub(r"<(br|p|div|li|h[1-6])[^>]*>", "\n", raw_html, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

BASE_URL = "https://www.courtlistener.com/api/rest/v4"


def _entry_page_left_window(results: list, until_date: str) -> bool:
    """True when this newest-first page has reached filings older than the window."""
    if not until_date:
        return False
    for row in results or []:
        if not isinstance(row, dict):
            continue
        day = str(row.get("date_filed") or "")[:10]
        if len(day) == 10 and day[4] == "-" and day[7] == "-" and day < until_date:
            return True
    return False


def _search_page_left_window(results: list, until_entry_before: Optional[str]) -> bool:
    """True when every dated hit on the page is older than the filing window."""
    if not until_entry_before:
        return False
    dates = []
    for hit in results or []:
        if not isinstance(hit, dict):
            continue
        raw = hit.get("entry_date_filed") or hit.get("entryDateFiled") or ""
        day = str(raw).strip()[:10]
        if len(day) == 10 and day[4] == "-" and day[7] == "-":
            dates.append(day)
    if not dates:
        return False
    return all(day < until_entry_before for day in dates)

# Florida federal district courts. Criminal bond-risk scans stay inside this set.
FL_FEDERAL_COURT_IDS = ("flmd", "flsd", "flnd")

# CourtListener v4 returns 401 for anonymous search and docket calls.
ANONYMOUS_V4_REJECTED = (
    "COURTLISTENER_API_TOKEN is unset. Anonymous CourtListener v4 "
    "search and docket calls are rejected (401)."
)

# Field selection keeps docket, entry, and party payloads small. Entry
# responses otherwise nest RECAP plain_text. A party's date of birth lives
# on the docket-specific party type, not on the legacy Party.extra_info
# field and not on a flat date_of_birth.
DOCKET_BRIEF_FIELDS = (
    "id,court_id,docket_number,case_name,case_name_full,date_filed,"
    "date_last_filing,cause,nature_of_suit,pacer_case_id,assigned_to_str"
)
DOCKET_ENTRY_FIELDS = "id,date_filed,description,entry_number,recap_documents__id"
DOCKET_PARTY_FIELDS = "id,name,party_types"


def courts_for_state(state: str) -> list:
    """Get court IDs for a specific state."""
    state = state.upper()
    return [cid for cid, meta in SE_US_COURTS.items() if meta["state"] == state]


def courts_for_states(states: list) -> list:
    """Get court IDs for multiple states."""
    states_upper = [s.upper() for s in states]
    return [cid for cid, meta in SE_US_COURTS.items() if meta["state"] in states_upper]


class CourtListenerQuotaBlocked(Exception):
    """Raised when a caller-supplied request gate denies an HTTP attempt."""


class CourtListenerClient:
    """Async client for CourtListener REST API v4.

    FLP-compliant: proper User-Agent with contact URL, retry with
    exponential backoff on 429/5xx, maintenance-window awareness,
    and bail-relevant opinion filtering.
    """

    def __init__(self, api_token: Optional[str] = None, *, request_gate=None):
        self.api_token = api_token
        self._request_gate = request_gate
        self._headers = {
            "User-Agent": "ShamrockLeads/1.0 (https://shamrockbailbonds.biz; admin@shamrockbailbonds.biz)",
        }
        if api_token:
            self._headers["Authorization"] = f"Token {api_token}"
        self._client: Optional[httpx.AsyncClient] = None
        # API health tracking
        self._requests_made = 0
        self._errors = 0
        self._rate_limited = 0

    def set_request_gate(self, gate) -> None:
        """Install or clear an async callable ``gate(attempt, min_delay) -> bool`` that reserves capacity and awaits pacing/backoff before dispatch."""
        self._request_gate = gate

    async def _check_request_gate(self, attempt: int, min_delay: float = 0.0) -> bool:
        if self._request_gate is None:
            return True
        return bool(await self._request_gate(attempt, min_delay))

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL, headers=self._headers,
                timeout=30.0, follow_redirects=True,
            )
        return self._client

    async def _request_with_retry(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Execute HTTP request with exponential backoff on 429/5xx."""
        client = await self._get_client()
        last_exc = None
        for attempt in range(MAX_RETRIES + 1):
            if attempt == 0 and not await self._check_request_gate(0, 0.0):
                raise CourtListenerQuotaBlocked("quota_exhausted")
            try:
                self._requests_made += 1
                resp = await getattr(client, method)(url, **kwargs)
                if resp.status_code == 429:
                    self._rate_limited += 1
                    if attempt < MAX_RETRIES:
                        wait = BACKOFF_BASE * (2 ** attempt)
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after and retry_after.isdigit():
                            wait = max(wait, int(retry_after))
                        if self._request_gate is not None:
                            if not await self._check_request_gate(attempt + 1, wait):
                                return resp
                            log.warning("Rate limited (429), retry after %.1fs gate wait (attempt %d/%d)", wait, attempt + 1, MAX_RETRIES)
                        else:
                            log.warning("Rate limited (429), retry in %.1fs (attempt %d/%d)", wait, attempt + 1, MAX_RETRIES)
                            await asyncio.sleep(wait)
                        continue
                    return resp
                if resp.status_code >= 500 and attempt < MAX_RETRIES:
                    self._errors += 1
                    wait = BACKOFF_BASE * (2 ** attempt)
                    if self._request_gate is not None:
                        if not await self._check_request_gate(attempt + 1, wait):
                            return resp
                        log.warning("Server error %d, retry after %.1fs gate wait", resp.status_code, wait)
                    else:
                        log.warning("Server error %d, retry in %.1fs", resp.status_code, wait)
                        await asyncio.sleep(wait)
                    continue
                return resp
            except (httpx.ConnectError, httpx.ReadTimeout) as e:
                self._errors += 1
                last_exc = e
                if attempt < MAX_RETRIES:
                    wait = BACKOFF_BASE * (2 ** attempt)
                    if self._request_gate is not None:
                        if not await self._check_request_gate(attempt + 1, wait):
                            raise
                        log.warning("Connection error, retry after %.1fs gate wait: %s", wait, str(e)[:80])
                    else:
                        log.warning("Connection error, retry in %.1fs: %s", wait, str(e)[:80])
                        await asyncio.sleep(wait)
        raise last_exc or httpx.ReadTimeout("Max retries exceeded")

    def _in_maintenance_window(self) -> bool:
        """Check if FLP is in scheduled maintenance (Thu 21:00-23:59 PT)."""
        now = datetime.now(timezone.utc)
        return (now.weekday() == _MAINTENANCE_DOW
                and _MAINTENANCE_START_HOUR_UTC <= now.hour < _MAINTENANCE_END_HOUR_UTC)

    def get_api_health(self) -> dict:
        """Return API health metrics."""
        return {
            "requests_made": self._requests_made,
            "errors": self._errors,
            "rate_limited": self._rate_limited,
            "in_maintenance": self._in_maintenance_window(),
        }

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def search_opinions(self, query="", courts=None,
                              date_filed_after=None, date_filed_before=None,
                              page_size=20, max_pages=5) -> dict:
        """Search case law opinions via the search endpoint.

        Uses cursor-based pagination (CourtListener v4) with retry logic.
        Respects FLP maintenance windows (Thu 21-23:59 PT).
        """
        if self._in_maintenance_window():
            log.info("Skipping search — FLP maintenance window active")
            return {"success": True, "count": 0, "results": [], "skipped": "maintenance"}

        params = {"type": "o", "page_size": page_size}
        if query:
            params["q"] = query
        if courts:
            params["court"] = " ".join(courts)
        if date_filed_after:
            params["filed_after"] = date_filed_after
        if date_filed_before:
            params["filed_before"] = date_filed_before

        all_results: list = []
        total_count: int = 0
        next_url: str | None = None
        page = 0

        try:
            while page < max_pages:
                if next_url:
                    resp = await self._request_with_retry("get", next_url)
                else:
                    resp = await self._request_with_retry("get", "/search/", params=params)
                resp.raise_for_status()
                data = resp.json()
                page_results = data.get("results", [])
                all_results.extend(page_results)
                if page == 0:
                    total_count = data.get("count", len(page_results))
                next_url = data.get("next")
                page += 1
                if not next_url:
                    break
                await asyncio.sleep(0.3)  # polite rate-limit between pages

            log.info(
                "CourtListener search: %d results (%d pages) for q='%s'",
                len(all_results), page, (query or "")[:50],
            )
            return {"success": True, "count": total_count, "results": all_results}
        except httpx.HTTPStatusError as e:
            log.error("CourtListener HTTP %d: %s", e.response.status_code, str(e)[:200])
            return {"success": False, "error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            log.error("CourtListener search error: %s", str(e)[:200])
            return {"success": False, "error": str(e)}

    @staticmethod
    def parse_cluster_id_from_url(url_or_id: str | int) -> Optional[int]:
        """Extract cluster_id from a CourtListener URL, API URI, or numeric string."""
        if url_or_id is None:
            return None
        raw = str(url_or_id).strip()
        if not raw:
            return None
        if raw.isdigit():
            return int(raw)
        # /opinion/2812209/obergefell-v-hodges/ or /clusters/2812209/
        m = re.search(r"/(?:opinion|clusters)/(\d+)", raw)
        if m:
            return int(m.group(1))
        # Fallback: trailing digits in path
        m = re.search(r"(\d+)/?$", raw)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _extract_id_from_uri(uri_or_val: str | int | None) -> Optional[int]:
        if not uri_or_val:
            return None
        s = str(uri_or_val).strip()
        if s.isdigit():
            return int(s)
        m = re.search(r"/(\d+)/?$", s)
        return int(m.group(1)) if m else None

    async def get_opinion_cluster(self, cluster_id: int) -> dict:
        """Fetch detailed opinion cluster metadata via /api/rest/v4/clusters/{id}/."""
        try:
            resp = await self._request_with_retry("get", f"/clusters/{int(cluster_id)}/")
            resp.raise_for_status()
            return {"success": True, "data": resp.json()}
        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_docket(self, docket_id: int) -> dict:
        """Fetch docket metadata via /api/rest/v4/dockets/{id}/."""
        try:
            resp = await self._request_with_retry("get", f"/dockets/{int(docket_id)}/")
            resp.raise_for_status()
            return {"success": True, "data": resp.json()}
        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _no_token_result(self) -> dict:
        """Structured skip when the v4 token is missing. No request is sent."""
        return {
            "success": False,
            "skipped": "no_token",
            "error": "no_token",
            "status_code": 401,
            "results": [],
            "data": None,
            "warning": ANONYMOUS_V4_REJECTED,
        }

    async def _read_v4(self, url: str, params: Optional[dict] = None) -> dict:
        """One GET. 429/5xx backoff lives on ``_request_with_retry``.

        Callers that follow ``next`` must cap the number of calls. This method
        itself never requests a second page.
        """
        if not (self.api_token or "").strip():
            return self._no_token_result()
        if self._in_maintenance_window():
            return {
                "success": False,
                "skipped": "maintenance",
                "error": "maintenance",
                "status_code": None,
                "results": [],
                "data": None,
            }
        try:
            request_kwargs = {"params": params} if params else {}
            resp = await self._request_with_retry("get", url, **request_kwargs)
        except CourtListenerQuotaBlocked:
            return {
                "success": False,
                "skipped": "quota_exhausted",
                "error": "quota_exhausted",
                "status_code": None,
                "results": [],
                "data": None,
            }
        except Exception as exc:
            log.warning("CourtListener request failed: %s", type(exc).__name__)
            return {
                "success": False,
                "error": type(exc).__name__,
                "status_code": None,
                "results": [],
                "data": None,
            }
        code = int(getattr(resp, "status_code", 0) or 0)
        if code == 401:
            return {
                "success": False,
                "error": "unauthorized",
                "status_code": 401,
                "results": [],
                "data": None,
                "warning": "CourtListener returned 401 for an authenticated v4 call.",
            }
        if code != 200:
            return {
                "success": False,
                "error": f"HTTP {code}",
                "status_code": code,
                "results": [],
                "data": None,
            }
        try:
            data = resp.json()
        except Exception:
            return {
                "success": False,
                "error": "bad_json",
                "status_code": code,
                "results": [],
                "data": None,
            }
        results = data.get("results") if isinstance(data, dict) else None
        return {
            "success": True,
            "status_code": 200,
            "data": data,
            "results": list(results or []),
        }

    async def search_limited(
        self,
        *,
        search_type: str,
        query: str,
        courts: Optional[list] = None,
        page_size: int = 15,
    ) -> dict:
        """One page of ``/search/``. Never follows ``next``.

        ``type=r`` is federal dockets with nested RECAP documents, ``type=rd``
        is RECAP documents, and ``type=d`` is federal dockets.
        """
        stype = (search_type or "").strip().lower()
        if stype not in {"o", "r", "rd", "d", "p", "oa"}:
            return {
                "success": False,
                "error": "bad_search_type",
                "status_code": None,
                "results": [],
                "data": None,
            }
        params = {
            "type": stype,
            "q": (query or "").strip(),
            "page_size": max(1, min(int(page_size or 15), 20)),
        }
        if courts:
            params["court"] = " ".join(str(c).strip() for c in courts if str(c).strip())
        return await self._read_v4("/search/", params)

    async def search_recent(
        self,
        *,
        search_type: str,
        query: str,
        courts: Optional[list] = None,
        filed_after: Optional[str] = None,
        entry_date_filed_after: Optional[str] = None,
        entry_date_filed_before: Optional[str] = None,
        start_url: Optional[str] = None,
        page_size: int = 20,
        max_pages: int = 50,
        pause_s: float = 0.0,
        until_entry_before: Optional[str] = None,
    ) -> dict:
        """Recent ``/search/`` hits inside a caller-supplied window.

        ``entry_date_filed_after`` is the docket-entry date. ``filed_after`` is
        the case-initiation date and is not used for bond-risk scans. Follows
        ``next`` until the cursor ends, a page is entirely older than
        ``until_entry_before``, or ``max_pages`` (safety ceiling 50) is hit.
        429 backoff is ``_request_with_retry``.
        """
        stype = (search_type or "").strip().lower()
        if stype not in {"o", "r", "rd", "d", "p", "oa"}:
            return {
                "success": False,
                "error": "bad_search_type",
                "status_code": None,
                "results": [],
                "data": None,
                "pages_fetched": 0,
            }
        page_cap = max(1, min(int(max_pages or 1), 50))
        params: Optional[dict] = {
            "type": stype,
            "q": (query or "").strip(),
            "page_size": max(1, min(int(page_size or 20), 20)),
            "order_by": "entry_date_filed desc",
        }
        if entry_date_filed_after:
            params["entry_date_filed_after"] = entry_date_filed_after
        if entry_date_filed_before:
            params["entry_date_filed_before"] = entry_date_filed_before
        if filed_after:
            params["filed_after"] = filed_after
        if courts:
            params["court"] = " ".join(str(c).strip() for c in courts if str(c).strip())

        url = start_url or "/search/"
        if start_url:
            params = None
        combined: list = []
        pages = 0
        last_data: dict = {}
        last: dict = {"success": False, "results": [], "status_code": None, "data": None}
        for _ in range(page_cap):
            last = await self._read_v4(url, params)
            pages += 1
            if not last.get("success"):
                if last.get("skipped") == "quota_exhausted":
                    if pages > 1:
                        return {
                            "success": True,
                            "status_code": 200,
                            "results": combined,
                            "data": last_data,
                            "pages_fetched": pages - 1,
                            "truncated": True,
                            "resume_url": url,
                            "quota_exhausted": True,
                        }
                    last["results"] = []
                    last["pages_fetched"] = 0
                    return last
                last["results"] = combined
                last["pages_fetched"] = pages if not combined else pages - 1
                return last
            page_rows = list(last.get("results") or [])
            combined.extend(page_rows)
            data = last.get("data") if isinstance(last.get("data"), dict) else {}
            last_data = data
            next_url = str((data or {}).get("next") or "").strip()
            if _search_page_left_window(page_rows, until_entry_before):
                return {
                    "success": True,
                    "status_code": 200,
                    "results": combined,
                    "data": data,
                    "pages_fetched": pages,
                    "truncated": False,
                }
            truncated = bool(next_url) and pages >= page_cap
            if not next_url or truncated:
                payload = {
                    "success": True,
                    "status_code": 200,
                    "results": combined,
                    "data": data,
                    "pages_fetched": pages,
                    "truncated": truncated,
                }
                if truncated and next_url:
                    payload["resume_url"] = next_url
                return payload
            url = next_url
            params = None
            if pause_s and self._request_gate is None:
                await asyncio.sleep(pause_s)
        return {
            "success": True,
            "status_code": 200,
            "results": combined,
            "pages_fetched": pages,
            "truncated": True,
        }

    async def get_docket_brief(self, docket_id: int) -> dict:
        """Docket metadata with field selection. One document, no related rows."""
        params = {"fields": DOCKET_BRIEF_FIELDS}
        return await self._read_v4(f"/dockets/{int(docket_id)}/", params)

    async def list_docket_entries(
        self,
        docket_id: int,
        page_size: int = 15,
        max_pages: int = 1,
        pause_s: float = 0.0,
        until_date: Optional[str] = None,
    ) -> dict:
        """Newest docket entries, through the caller's filing window.

        ``max_pages`` is a safety ceiling (at most 50). No plain_text. The
        first request selects fields. A later page uses the ``next`` URL only.
        Paging stops once a page contains a filing older than ``until_date``.
        ``truncated`` is true only when the ceiling is hit while newer filings
        may remain.
        """
        page_cap = max(1, min(int(max_pages or 1), 50))
        params: Optional[dict] = {
            "docket": int(docket_id),
            "fields": DOCKET_ENTRY_FIELDS,
            "order_by": "-date_filed",
            "page_size": max(1, min(int(page_size or 15), 20)),
        }
        url = "/docket-entries/"
        combined: list = []
        pages = 0
        window_end = str(until_date or "")[:10]
        for _ in range(page_cap):
            res = await self._read_v4(url, params)
            pages += 1
            if not res.get("success"):
                if res.get("skipped") == "quota_exhausted":
                    res["results"] = combined or []
                    res["pages_fetched"] = pages - 1
                    return res
                res["results"] = combined or res.get("results") or []
                res["pages_fetched"] = pages if not combined else pages - 1
                return res
            page_rows = list(res.get("results") or [])
            combined.extend(page_rows)
            data = res.get("data") if isinstance(res.get("data"), dict) else {}
            next_url = str((data or {}).get("next") or "").strip()
            if _entry_page_left_window(page_rows, window_end):
                return {
                    "success": True,
                    "status_code": 200,
                    "results": combined,
                    "data": data,
                    "pages_fetched": pages,
                    "truncated": False,
                }
            if not next_url or pages >= page_cap:
                return {
                    "success": True,
                    "status_code": 200,
                    "results": combined,
                    "data": data,
                    "pages_fetched": pages,
                    "truncated": bool(next_url) and pages >= page_cap,
                }
            url = next_url
            params = None
            if pause_s and self._request_gate is None:
                await asyncio.sleep(pause_s)
        return {
            "success": True,
            "status_code": 200,
            "results": combined,
            "pages_fetched": pages,
            "truncated": True,
        }

    async def list_docket_parties(
        self,
        docket_id: int,
        page_size: int = 20,
        max_pages: int = 1,
        pause_s: float = 0.0,
    ) -> dict:
        """Party names for one docket. Follows ``next`` up to ``max_pages`` (ceiling 50)."""
        page_cap = max(1, min(int(max_pages or 1), 50))
        params: Optional[dict] = {
            "docket": int(docket_id),
            "fields": DOCKET_PARTY_FIELDS,
            "filter_nested_results": "true",
            "page_size": max(1, min(int(page_size or 20), 20)),
        }
        url = "/parties/"
        combined: list = []
        pages = 0
        for _ in range(page_cap):
            res = await self._read_v4(url, params)
            pages += 1
            if not res.get("success"):
                if res.get("skipped") == "quota_exhausted":
                    res["results"] = combined or []
                    res["pages_fetched"] = pages - 1
                    return res
                res["results"] = combined or res.get("results") or []
                res["pages_fetched"] = pages if not combined else pages - 1
                return res
            combined.extend(res.get("results") or [])
            data = res.get("data") if isinstance(res.get("data"), dict) else {}
            next_url = str((data or {}).get("next") or "").strip()
            if not next_url or pages >= page_cap:
                return {
                    "success": True,
                    "status_code": 200,
                    "results": combined,
                    "data": data,
                    "pages_fetched": pages,
                    "truncated": bool(next_url) and pages >= page_cap,
                }
            url = next_url
            params = None
            if pause_s and self._request_gate is None:
                await asyncio.sleep(pause_s)
        return {
            "success": True,
            "status_code": 200,
            "results": combined,
            "pages_fetched": pages,
            "truncated": True,
        }

    async def get_opinion(self, opinion_id: int) -> dict:
        """Fetch opinion text and citations via /api/rest/v4/opinions/{id}/.

        Uses field selection and prefers `html_with_citations` over `plain_text`
        per FLP v4 Case Law documentation.
        """
        params = {
            "fields": (
                "id,type,author_str,per_curiam,html_with_citations,plain_text,"
                "opinions_cited,cluster,download_url,local_path"
            )
        }
        try:
            resp = await self._request_with_retry(
                "get", f"/opinions/{int(opinion_id)}/", params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            raw_html = data.get("html_with_citations") or ""
            plain = data.get("plain_text") or ""
            clean_text = _strip_html_text(raw_html) if raw_html else plain.strip()
            data["clean_text"] = clean_text
            data["opinions_cited_count"] = len(data.get("opinions_cited") or [])
            return {"success": True, "data": data}
        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_full_case_dossier(self, cluster_id_or_url: str | int) -> dict:
        """Assemble full 4-tier CourtListener v4 case dossier (Court -> Docket -> Cluster -> Opinion)."""
        cluster_id = self.parse_cluster_id_from_url(cluster_id_or_url)
        if not cluster_id:
            return {"success": False, "error": "Invalid cluster ID or CourtListener URL"}

        if self._in_maintenance_window():
            return {"success": False, "error": "CourtListener FLP maintenance window active (Thu 21:00-23:59 PT)"}

        cluster_res = await self.get_opinion_cluster(cluster_id)
        if not cluster_res.get("success"):
            return {"success": False, "error": f"Cluster lookup failed: {cluster_res.get('error')}"}

        cluster = cluster_res["data"]
        docket_id = self._extract_id_from_uri(cluster.get("docket"))
        docket_data = {}
        if docket_id:
            docket_res = await self.get_docket(docket_id)
            if docket_res.get("success"):
                docket_data = docket_res["data"]

        sub_opinions = cluster.get("sub_opinions") or []
        opinion_data = {}
        if sub_opinions:
            first_op_id = self._extract_id_from_uri(sub_opinions[0])
            if first_op_id:
                op_res = await self.get_opinion(first_op_id)
                if op_res.get("success"):
                    opinion_data = op_res["data"]

        court_id = (
            docket_data.get("court_id")
            or self._extract_court_slug(docket_data.get("court"))
            or ""
        )
        court_meta = SE_US_COURTS.get(court_id, {})

        case_name = cluster.get("case_name") or docket_data.get("case_name") or "Unknown Case"
        opinion_text = opinion_data.get("clean_text") or cluster.get("syllabus") or cluster.get("headmatter") or ""
        disposition = self._classify_disposition(opinion_text[:4000], case_name)

        cite_list = cluster.get("citations") or []
        formatted_cites = []
        for c in cite_list:
            if isinstance(c, dict):
                formatted_cites.append(
                    " ".join(str(x) for x in (c.get("volume"), c.get("reporter"), c.get("page")) if x)
                )
            elif isinstance(c, str):
                formatted_cites.append(c)

        opinion_summary_for_score = {
            "disposition": disposition,
            "citation_count": cluster.get("citation_count") or 0,
        }
        bail_impact = score_bail_impact(opinion_summary_for_score)

        return {
            "success": True,
            "dossier": {
                "cluster_id": cluster_id,
                "source_id": str(cluster_id),
                "case_name": case_name,
                "case_name_full": cluster.get("case_name_full") or docket_data.get("case_name_full") or case_name,
                "absolute_url": cluster.get("absolute_url") or docket_data.get("absolute_url") or f"/opinion/{cluster_id}/",
                "court": {
                    "id": court_id,
                    "name": court_meta.get("name") or court_id or "CourtListener Court",
                    "state": court_meta.get("state", ""),
                    "type": court_meta.get("type", ""),
                    "tier": court_meta.get("tier", ""),
                },
                "docket": {
                    "id": docket_data.get("id") or docket_id,
                    "docket_number": docket_data.get("docket_number") or "",
                    "pacer_case_id": docket_data.get("pacer_case_id") or "",
                    "date_filed": docket_data.get("date_filed") or cluster.get("date_filed") or "",
                    "date_terminated": docket_data.get("date_terminated") or "",
                    "date_last_filing": docket_data.get("date_last_filing") or "",
                    "assigned_to_str": docket_data.get("assigned_to_str") or "",
                    "referred_to_str": docket_data.get("referred_to_str") or "",
                    "cause": docket_data.get("cause") or "",
                    "nature_of_suit": docket_data.get("nature_of_suit") or "",
                },
                "cluster": {
                    "id": cluster_id,
                    "date_filed": cluster.get("date_filed") or "",
                    "judges": cluster.get("judges") or "",
                    "precedential_status": cluster.get("precedential_status") or "",
                    "citation_count": cluster.get("citation_count") or 0,
                    "citations": formatted_cites,
                    "sub_opinions_count": len(sub_opinions),
                    "syllabus": cluster.get("syllabus") or "",
                },
                "opinion": {
                    "id": opinion_data.get("id"),
                    "type": opinion_data.get("type") or "",
                    "author_str": opinion_data.get("author_str") or "",
                    "per_curiam": bool(opinion_data.get("per_curiam")),
                    "opinions_cited_count": opinion_data.get("opinions_cited_count") or 0,
                    "download_url": opinion_data.get("download_url") or "",
                    "text": opinion_text[:18000],
                    "text_truncated": len(opinion_text) > 18000,
                },
                "disposition": disposition,
                "disposition_category": classify_disposition_category(disposition),
                "bail_impact": bail_impact,
            },
            "api_health": self.get_api_health(),
        }

    @staticmethod
    def _extract_court_slug(court_uri: Optional[str]) -> str:
        if not court_uri:
            return ""
        m = re.search(r"/courts/([^/]+)/?$", str(court_uri))
        return m.group(1) if m else ""

    async def lookup_by_docket_number(
        self,
        docket_number: str,
        court: Optional[str] = None,
        state: Optional[str] = None,
    ) -> dict:
        """Resolve dockets, opinion clusters, and search hits by docket number."""
        dnum = (docket_number or "").strip()
        if not dnum:
            return {"success": False, "error": "docket_number is required"}

        if self._in_maintenance_window():
            return {"success": False, "error": "CourtListener FLP maintenance window active"}

        docket_params = {"docket_number": dnum, "page_size": 10}
        cluster_params = {"docket__docket_number": dnum, "page_size": 10}
        if court:
            docket_params["court"] = court
            cluster_params["docket__court"] = court

        dockets, clusters, search_hits = [], [], []
        errors = []

        try:
            d_resp = await self._request_with_retry("get", "/dockets/", params=docket_params)
            if d_resp.status_code == 200:
                for d in d_resp.json().get("results") or []:
                    cid = d.get("court_id") or self._extract_court_slug(d.get("court"))
                    cmeta = SE_US_COURTS.get(cid, {})
                    dockets.append({
                        "id": d.get("id"),
                        "docket_number": d.get("docket_number") or "",
                        "case_name": d.get("case_name") or d.get("case_name_full") or "",
                        "court_id": cid,
                        "court_name": cmeta.get("name", cid),
                        "state": cmeta.get("state", ""),
                        "date_filed": d.get("date_filed") or "",
                        "date_terminated": d.get("date_terminated") or "",
                        "date_last_filing": d.get("date_last_filing") or "",
                        "assigned_to_str": d.get("assigned_to_str") or "",
                        "cause": d.get("cause") or "",
                        "nature_of_suit": d.get("nature_of_suit") or "",
                        "absolute_url": d.get("absolute_url") or "",
                        "clusters_count": len(d.get("clusters") or []),
                    })
            else:
                errors.append(f"dockets HTTP {d_resp.status_code}")
        except Exception as e:
            errors.append(f"dockets: {e}")

        try:
            c_resp = await self._request_with_retry("get", "/clusters/", params=cluster_params)
            if c_resp.status_code == 200:
                for c in c_resp.json().get("results") or []:
                    did = self._extract_id_from_uri(c.get("docket"))
                    clusters.append({
                        "id": c.get("id"),
                        "cluster_id": c.get("id"),
                        "docket_id": did,
                        "case_name": c.get("case_name") or "",
                        "date_filed": c.get("date_filed") or "",
                        "judges": c.get("judges") or "",
                        "precedential_status": c.get("precedential_status") or "",
                        "citation_count": c.get("citation_count") or 0,
                        "absolute_url": c.get("absolute_url") or "",
                    })
            else:
                errors.append(f"clusters HTTP {c_resp.status_code}")
        except Exception as e:
            errors.append(f"clusters: {e}")

        target_courts = [court] if court else (courts_for_state(state) if state else None)
        s_res = await self.live_search(
            query=f'"{dnum}"',
            search_type="o",
            courts=target_courts,
            docket_number=dnum,
            page_size=10,
        )
        if s_res.get("success"):
            search_hits = s_res.get("results") or []

        return {
            "success": True,
            "docket_number": dnum,
            "dockets": dockets,
            "clusters": clusters,
            "search_hits": search_hits,
            "warnings": errors,
            "api_health": self.get_api_health(),
        }

    async def live_search(
        self,
        query: str = "",
        search_type: str = "o",
        courts: Optional[list] = None,
        state: Optional[str] = None,
        date_filed_after: Optional[str] = None,
        date_filed_before: Optional[str] = None,
        docket_number: Optional[str] = None,
        judge: Optional[str] = None,
        page_size: int = 25,
    ) -> dict:
        """Execute a live CourtListener v4 /search/ query with instant bail analytics."""
        if self._in_maintenance_window():
            return {
                "success": True,
                "count": 0,
                "results": [],
                "analytics": self._summarize_search_results([]),
                "skipped": "maintenance",
                "api_health": self.get_api_health(),
            }

        stype = (search_type or "o").strip().lower()
        if stype not in ("o", "r", "d", "p", "oa"):
            stype = "o"

        effective_courts = list(courts) if courts else []
        if not effective_courts and state:
            effective_courts = courts_for_state(state)

        params = {
            "type": stype,
            "page_size": min(max(int(page_size or 25), 1), 50),
        }
        if query:
            params["q"] = query.strip()
        if effective_courts:
            params["court"] = " ".join(effective_courts)
        if date_filed_after:
            params["filed_after"] = date_filed_after
        if date_filed_before:
            params["filed_before"] = date_filed_before
        if docket_number:
            params["docket_number"] = docket_number.strip()
        if judge:
            params["judge"] = judge.strip()

        try:
            resp = await self._request_with_retry("get", "/search/", params=params)
            resp.raise_for_status()
            data = resp.json()
            raw_results = data.get("results") or []
            total_count = int(data.get("count") or len(raw_results))

            normalized = []
            for raw in raw_results:
                item = self._normalize_search_hit(raw, search_type=stype)
                if item:
                    normalized.append(item)

            return {
                "success": True,
                "search_type": stype,
                "count": total_count,
                "returned": len(normalized),
                "results": normalized,
                "analytics": self._summarize_search_results(normalized),
                "api_health": self.get_api_health(),
            }
        except httpx.HTTPStatusError as e:
            log.error("CourtListener live_search HTTP %d: %s", e.response.status_code, str(e)[:200])
            return {"success": False, "error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            log.error("CourtListener live_search error: %s", str(e)[:200])
            return {"success": False, "error": str(e)}

    def _normalize_search_hit(self, raw: dict, search_type: str = "o") -> Optional[dict]:
        """Normalize a result from any /api/rest/v4/search/ type (o, r, d, p, oa)."""
        if search_type == "p":
            name = raw.get("name") or raw.get("name_full") or ""
            if not name:
                return None
            positions = raw.get("positions") or []
            pos_label = ""
            if positions and isinstance(positions[0], dict):
                p0 = positions[0]
                pos_label = " · ".join(
                    str(x) for x in (p0.get("position_type"), p0.get("court")) if x
                )
            cid = str(raw.get("court_id") or "")
            meta = SE_US_COURTS.get(cid, {})
            return {
                "source": "courtlistener",
                "search_type": "p",
                "source_id": str(raw.get("id") or ""),
                "case_name": name,
                "judges": name,
                "court_id": cid,
                "court_name": meta.get("name", pos_label or cid or "Federal/State Judiciary"),
                "state": meta.get("state", ""),
                "disposition": "judge_profile",
                "disposition_category": "other",
                "snippet": pos_label or raw.get("appointer") or raw.get("school") or "",
                "date_filed": raw.get("dob") or "",
                "absolute_url": raw.get("absolute_url") or "",
                "bail_impact": {"score": 0, "factors": []},
                "citation_count": 0,
            }

        cid = str(raw.get("court_id") or "")
        item = self._normalize_opinion(raw, cid)
        if not item:
            return None
        item["search_type"] = search_type
        item["bail_impact"] = score_bail_impact(item)
        item["disposition_category"] = classify_disposition_category(item.get("disposition", ""))
        return item

    @staticmethod
    def _summarize_search_results(results: list) -> dict:
        """Compute instant on-the-fly analytics across live search results."""
        disp_counts: dict[str, int] = {}
        court_counts: dict[str, int] = {}
        state_counts: dict[str, int] = {}
        judge_counts: dict[str, int] = {}
        scores = []
        total_citations = 0
        bail_critical_count = 0

        for r in results:
            disp = r.get("disposition") or "unknown"
            disp_counts[disp] = disp_counts.get(disp, 0) + 1
            if classify_disposition_category(disp) == "bail_critical":
                bail_critical_count += 1

            cname = r.get("court_name") or r.get("court_id") or "Unknown"
            court_counts[cname] = court_counts.get(cname, 0) + 1

            st = r.get("state") or "OTHER"
            state_counts[st] = state_counts.get(st, 0) + 1

            judge_raw = r.get("judges") or r.get("assigned_to_str") or ""
            if judge_raw:
                jname = str(judge_raw).split(",")[0].strip()
                if jname:
                    judge_counts[jname] = judge_counts.get(jname, 0) + 1

            score = (r.get("bail_impact") or {}).get("score", 0)
            scores.append(score)
            total_citations += int(r.get("citation_count") or 0)

        n = len(results)
        return {
            "total": n,
            "bail_critical_count": bail_critical_count,
            "bail_critical_pct": round((bail_critical_count / n) * 100, 1) if n else 0.0,
            "avg_bail_impact": round(sum(scores) / n, 1) if n else 0.0,
            "total_citations": total_citations,
            "by_disposition": [
                {"disposition": k, "count": v, "category": classify_disposition_category(k)}
                for k, v in sorted(disp_counts.items(), key=lambda x: -x[1])
            ],
            "by_court": [
                {"court": k, "count": v}
                for k, v in sorted(court_counts.items(), key=lambda x: -x[1])[:8]
            ],
            "by_state": [
                {"state": k, "count": v}
                for k, v in sorted(state_counts.items(), key=lambda x: -x[1])
            ],
            "by_judge": [
                {"judge": k, "count": v}
                for k, v in sorted(judge_counts.items(), key=lambda x: -x[1])[:8]
            ],
        }

    async def search_defendant(self, name: str, courts=None, days_back=365) -> dict:
        """Search for opinions mentioning a defendant name."""
        date_after = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
        return await self.search_opinions(
            query=f'"{name}"', courts=courts, date_filed_after=date_after,
        )

    async def ingest_recent_opinions(self, days_back=180, states=None, page_size=20,
                                     bail_relevant_only=True) -> list:
        """Pull recent opinions for ingestion into court_outcomes.

        Batches courts by state for efficiency (12 queries vs 67).
        Filters to bail/criminal-relevant cases when bail_relevant_only=True.
        Uses retry-safe requests and respects FLP maintenance windows.
        """
        if self._in_maintenance_window():
            log.info("Skipping ingestion — FLP maintenance window active")
            return []

        date_after = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
        target_states = [s.upper() for s in states] if states else SE_US_STATES

        all_results = []
        skipped_irrelevant = 0

        for state in target_states:
            state_courts = courts_for_state(state)
            if not state_courts:
                continue

            try:
                result = await self.search_opinions(
                    courts=state_courts,
                    date_filed_after=date_after,
                    page_size=page_size,
                    max_pages=3,
                )

                if result.get("success") and result.get("results"):
                    for r in result["results"]:
                        court_id = r.get("court_id", "")
                        if court_id not in SE_US_COURTS:
                            court_id = state_courts[0]
                        normalized = self._normalize_opinion(r, court_id)
                        if not normalized:
                            continue
                        # Filter to bail-relevant cases
                        if bail_relevant_only and not self._is_bail_relevant(normalized):
                            skipped_irrelevant += 1
                            continue
                        all_results.append(normalized)

                log.info("State %s: fetched %d results",
                         state, len(result.get("results", [])) if result.get("success") else 0)
                await asyncio.sleep(0.5)  # Rate-limit between states
            except Exception as e:
                log.warning("Ingestion skip for state %s: %s", state, str(e)[:100])

        log.info("Ingested %d bail-relevant opinions from %d states (skipped %d irrelevant)",
                 len(all_results), len(target_states), skipped_irrelevant)
        return all_results

    def _normalize_opinion(self, raw: dict, court_id: str) -> Optional[dict]:
        """Convert CourtListener search result to our court_outcomes schema."""
        try:
            case_name = raw.get("caseName") or raw.get("case_name") or ""
            if not case_name:
                return None
            meta = SE_US_COURTS.get(court_id, {})

            # v4 search nests opinion snippets inside `opinions` or RECAP docs inside `recap_documents`
            snippet = raw.get("snippet") or ""
            if not snippet and isinstance(raw.get("opinions"), list) and raw["opinions"]:
                snippet = raw["opinions"][0].get("snippet") or ""
            if not snippet and isinstance(raw.get("recap_documents"), list) and raw["recap_documents"]:
                d0 = raw["recap_documents"][0]
                snippet = d0.get("snippet") or d0.get("description") or ""
            if not snippet:
                snippet = " · ".join(
                    str(x) for x in (raw.get("cause"), raw.get("suitNature"), raw.get("nature_of_suit")) if x
                )

            # Strip search highlight tags if returned in HTML snippet
            clean_snippet = _strip_html_text(snippet) if "<" in snippet else snippet
            disposition = self._classify_disposition(clean_snippet, case_name)

            cluster_id = raw.get("cluster_id") or raw.get("id") or raw.get("docket_id") or ""
            source_id = str(cluster_id or raw.get("id") or raw.get("docket_id") or "")
            assigned_to = raw.get("assignedTo") or raw.get("assigned_to_str") or ""
            referred_to = raw.get("referredTo") or raw.get("referred_to_str") or ""
            judges = raw.get("judge") or assigned_to or ""

            return {
                "source": "courtlistener",
                "source_id": source_id,
                "cluster_id": cluster_id,
                "docket_id": raw.get("docket_id") or "",
                "court_id": court_id,
                "court_name": meta.get("name") or raw.get("court") or court_id or "Unknown Court",
                "state": meta.get("state", ""),
                "case_name": case_name,
                "docket_number": raw.get("docketNumber") or raw.get("docket_number") or "",
                "date_filed": raw.get("dateFiled") or raw.get("date_filed") or raw.get("dateArgued") or "",
                "disposition": disposition,
                "snippet": clean_snippet[:500] if clean_snippet else "",
                "judges": judges,
                "assigned_to_str": assigned_to,
                "referred_to_str": referred_to,
                "citation": raw.get("citation") or [],
                "citation_count": int(raw.get("citeCount") or raw.get("citation_count") or 0),
                "absolute_url": raw.get("absolute_url") or "",
                "suit_nature": raw.get("suitNature") or raw.get("nature_of_suit") or "",
                "status": raw.get("status", ""),
                "court_type": meta.get("type", "state"),
                "court_tier": meta.get("tier", "unknown"),
                "jurisdiction": meta.get("state", ""),
                "ingested_at": datetime.now(timezone.utc).isoformat() + "Z",
            }
        except Exception:
            return None

    @staticmethod
    def _classify_disposition(text: str, case_name: str = "") -> str:
        """Classify court disposition from opinion text/snippet.

        Enhanced with bail-specific patterns for accurate bond intelligence.
        Priority-ordered: most specific patterns first.
        """
        t = (text + " " + case_name).lower()
        # ── Bail-Specific (highest priority) ────────────────────────────────
        if re.search(r"bond\s+(forfeited|estreated|forfeiture)", t):
            return "bond_forfeiture"
        if re.search(r"motion\s+to\s+revoke\s+bond|bond\s+revoc", t):
            return "bond_revoked"
        if re.search(r"failure\s+to\s+appear|\bfta\b", t):
            return "fta"
        if re.search(r"bond\s+(reduced|lowered|decreased)", t):
            return "bond_reduced"
        if re.search(r"bond\s+(increased|raised)", t):
            return "bond_increased"
        # ── Standard Dispositions ───────────────────────────────────────────
        if "acquit" in t or "not guilty" in t:
            return "acquittal"
        if "plea" in t and ("guilty" in t or "nolo" in t):
            return "plea"
        if re.search(r"nolle\s+prosequi|nol\s*pros", t):
            return "nolle_prosequi"
        if "dismiss" in t:
            return "dismissed"
        if re.search(r"probation\s+violat|\bvop\b", t):
            return "probation_violation"
        if "reversed" in t or "reverse" in t:
            return "reversed"
        if "remand" in t:
            return "remanded"
        if "affirm" in t:
            return "affirmed"
        if "convict" in t or ("guilty" in t and "not guilty" not in t):
            return "conviction"
        if "sentence" in t:
            return "sentencing"
        if "vacat" in t:
            return "vacated"
        if "denied" in t:
            return "denied"
        if re.search(r"pretrial\s+(release|detention|diversion)", t):
            return "pretrial_order"
        return "unknown"

    @staticmethod
    def _is_bail_relevant(opinion: dict) -> bool:
        """Filter opinions to bail/criminal law relevance.

        Excludes civil, family, contract, administrative, and other
        non-criminal matters that don't inform bail bond intelligence.
        """
        searchable = " ".join(filter(None, [
            opinion.get("case_name", ""),
            opinion.get("snippet", ""),
            opinion.get("disposition", ""),
        ]))
        # Criminal case name patterns ("State v.", "People v.", etc.)
        if re.search(r"state\s+(?:of\s+\w+\s+)?v\.", searchable, re.I):
            return True
        if re.search(r"people\s+v\.|united\s+states\s+v\.|commonwealth\s+v\.", searchable, re.I):
            return True
        # Bail-specific keyword match
        if _BAIL_KEYWORDS.search(searchable):
            return True
        # Known bail-relevant dispositions
        disp = opinion.get("disposition", "")
        if disp in ("bond_forfeiture", "bond_revoked", "fta", "bond_reduced",
                    "bond_increased", "probation_violation", "pretrial_order"):
            return True
        return False

    def get_coverage_summary(self) -> dict:
        """Return coverage stats for the SE US court registry."""
        by_state = {}
        for cid, meta in SE_US_COURTS.items():
            st = meta["state"]
            if st not in by_state:
                by_state[st] = {"state": st, "courts": [], "state_count": 0, "federal_count": 0}
            by_state[st]["courts"].append({"id": cid, **meta})
            if meta["type"] == "state":
                by_state[st]["state_count"] += 1
            else:
                by_state[st]["federal_count"] += 1
        return {
            "total_courts": len(SE_US_COURTS),
            "total_states": len(SE_US_STATES),
            "states": SE_US_STATES,
            "by_state": list(by_state.values()),
        }
