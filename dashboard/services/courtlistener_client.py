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
from datetime import datetime, timedelta
from typing import Optional

import httpx

log = logging.getLogger("shamrock.courtlistener")

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
    """Async client for CourtListener REST API v4."""

    def __init__(self, api_token: Optional[str] = None):
        self.api_token = api_token
        self._headers = {"User-Agent": "ShamrockLeads/1.0 (admin@shamrockbailbonds.biz)"}
        if api_token:
            self._headers["Authorization"] = f"Token {api_token}"
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL, headers=self._headers,
                timeout=30.0, follow_redirects=True,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def search_opinions(self, query="", courts=None,
                              date_filed_after=None, date_filed_before=None,
                              page_size=20) -> dict:
        """Search case law opinions via the search endpoint."""
        client = await self._get_client()
        params = {"type": "o"}
        if query:
            params["q"] = query
        if courts:
            params["court"] = " ".join(courts)
        if date_filed_after:
            params["filed_after"] = date_filed_after
        if date_filed_before:
            params["filed_before"] = date_filed_before
        try:
            resp = await client.get("/search/", params=params)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            log.info("CourtListener search: %d results for q='%s'", len(results), query[:50])
            return {"success": True, "count": data.get("count", len(results)), "results": results}
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
        date_after = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        return await self.search_opinions(
            query=f'"{name}"', courts=courts, date_filed_after=date_after,
        )

    async def ingest_recent_opinions(self, days_back=30, states=None, page_size=20) -> list:
        """Pull recent opinions for ingestion into court_outcomes.

        Args:
            days_back: How many days back to search
            states: List of state codes (defaults to all SE US)
            page_size: Max results per court
        """
        date_after = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        if states:
            target_courts = courts_for_states(states)
        else:
            target_courts = ALL_COURT_IDS
        all_results = []
        for court_id in target_courts:
            try:
                result = await self.search_opinions(
                    courts=[court_id], date_filed_after=date_after, page_size=page_size,
                )
                if result.get("success") and result.get("results"):
                    for r in result["results"]:
                        normalized = self._normalize_opinion(r, court_id)
                        if normalized:
                            all_results.append(normalized)
                await asyncio.sleep(0.5)  # Rate-limit
            except Exception as e:
                log.warning("Ingestion skip for court %s: %s", court_id, str(e)[:100])
        log.info("Ingested %d opinions from %d courts", len(all_results), len(target_courts))
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
                "ingested_at": datetime.utcnow().isoformat() + "Z",
            }
        except Exception:
            return None

    @staticmethod
    def _classify_disposition(text: str, case_name: str = "") -> str:
        """Classify court disposition from opinion text/snippet."""
        t = (text + " " + case_name).lower()
        if "acquit" in t or "not guilty" in t:
            return "acquittal"
        if "plea" in t and ("guilty" in t or "nolo" in t):
            return "plea"
        if "dismiss" in t:
            return "dismissed"
        if "reversed" in t or "reverse" in t:
            return "reversed"
        if "remand" in t:
            return "remanded"
        if "affirm" in t:
            return "affirmed"
        if "convict" in t or "guilty" in t:
            return "conviction"
        if "sentence" in t:
            return "conviction"
        if "vacat" in t:
            return "vacated"
        if "denied" in t:
            return "denied"
        return "unknown"

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
