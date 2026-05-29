"""
CourtListener REST API Client — ShamrockLeads Intelligence Suite
================================================================

Interfaces with CourtListener.com v4 API to pull court opinions
across the Southeast United States. Covers 12 states:
  FL, GA, AL, MS, LA, TN, KY, NC, SC, VA, WV, AR

Data feeds into `court_outcomes` MongoDB collection for empirical
FTA/conviction modeling and the Court Outcome Predictor.

See: https://www.courtlistener.com/api/rest/v4/
"""

import logging
import asyncio
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
    # ── Federal Appellate (covering SE US circuits) ──────────────────────────
    "ca4":              {"name": "4th Circuit (MD,VA,WV,NC,SC)",   "state": "MULTI", "type": "federal", "tier": "circuit"},
    "ca5":              {"name": "5th Circuit (LA,MS,TX)",         "state": "MULTI", "type": "federal", "tier": "circuit"},
    "ca6":              {"name": "6th Circuit (KY,TN,OH,MI)",      "state": "MULTI", "type": "federal", "tier": "circuit"},
    "ca11":             {"name": "11th Circuit (FL,GA,AL)",        "state": "MULTI", "type": "federal", "tier": "circuit"},
}

# Convenience lookups
ALL_COURT_IDS = list(SE_US_COURTS.keys())
SE_US_STATES = ["FL", "GA", "AL", "MS", "LA", "TN", "KY", "NC", "SC", "VA", "WV", "AR"]

BASE_URL = "https://www.courtlistener.com/api/rest/v4"


def courts_for_state(state: str) -> list:
    """Get court IDs for a specific state."""
    state = state.upper()
    return [cid for cid, meta in SE_US_COURTS.items() if meta["state"] == state]


def courts_for_states(states: list) -> list:
    """Get court IDs for multiple states."""
    states_upper = [s.upper() for s in states]
    return [cid for cid, meta in SE_US_COURTS.items() if meta["state"] in states_upper]


class CourtListenerClient:
    """Async client for CourtListener REST API v4.

    FLP-compliant: proper User-Agent with contact URL, retry with
    exponential backoff on 429/5xx, maintenance-window awareness,
    and bail-relevant opinion filtering.
    """

    def __init__(self, api_token: Optional[str] = None):
        self.api_token = api_token
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
            try:
                self._requests_made += 1
                resp = await getattr(client, method)(url, **kwargs)
                if resp.status_code == 429:
                    self._rate_limited += 1
                    wait = BACKOFF_BASE * (2 ** attempt)
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        wait = max(wait, int(retry_after))
                    log.warning("Rate limited (429), retry in %.1fs (attempt %d/%d)", wait, attempt + 1, MAX_RETRIES)
                    await asyncio.sleep(wait)
                    continue
                if resp.status_code >= 500 and attempt < MAX_RETRIES:
                    self._errors += 1
                    wait = BACKOFF_BASE * (2 ** attempt)
                    log.warning("Server error %d, retry in %.1fs", resp.status_code, wait)
                    await asyncio.sleep(wait)
                    continue
                return resp
            except (httpx.ConnectError, httpx.ReadTimeout) as e:
                self._errors += 1
                last_exc = e
                if attempt < MAX_RETRIES:
                    wait = BACKOFF_BASE * (2 ** attempt)
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

    async def get_opinion_cluster(self, cluster_id: int) -> dict:
        """Fetch detailed opinion cluster metadata."""
        client = await self._get_client()
        try:
            resp = await client.get(f"/clusters/{cluster_id}/")
            resp.raise_for_status()
            return {"success": True, "data": resp.json()}
        except Exception as e:
            return {"success": False, "error": str(e)}

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
            snippet = raw.get("snippet", "")
            disposition = self._classify_disposition(snippet, case_name)
            return {
                "source": "courtlistener",
                "source_id": str(raw.get("id", "")),
                "court_id": court_id,
                "court_name": meta.get("name", court_id),
                "state": meta.get("state", ""),
                "case_name": case_name,
                "docket_number": raw.get("docketNumber") or raw.get("docket_number") or "",
                "date_filed": raw.get("dateFiled") or raw.get("date_filed") or "",
                "disposition": disposition,
                "snippet": snippet[:500] if snippet else "",
                "judges": raw.get("judge", ""),
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
