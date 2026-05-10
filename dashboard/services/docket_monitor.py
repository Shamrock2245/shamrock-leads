"""
Docket Monitor — ShamrockLeads Intelligence Suite
===================================================
Real-time court docket surveillance for every active bond.
Queries CourtListener RECAP + Docket API to detect:
  FTA warrants, motions to revoke bond, bench warrants,
  sentencing, continuances, dismissals, bond modifications.

Each event is classified by severity and stored in `docket_events`.
High-severity events fire Slack alerts and update the forfeiture predictor.

MongoDB Collection: docket_events
"""
import logging, asyncio, re, os
from datetime import datetime, timedelta
from typing import Optional
import httpx

log = logging.getLogger("shamrock.docket_monitor")

# ── Event Classification Rules ──────────────────────────────────────────────
# (regex, event_type, severity, risk_adjustment, description)
DOCKET_EVENT_PATTERNS = [
    # CRITICAL
    (r"warrant\s+(issued|for\s+arrest|bench|capias)", "bench_warrant", "critical", 0.35,
     "Bench warrant / capias issued"),
    (r"failure\s+to\s+appear|fta|failed\s+to\s+appear", "fta_warrant", "critical", 0.40,
     "Failure to Appear detected — forfeiture imminent"),
    (r"motion\s+to\s+revoke\s+bond|bond\s+revoc|revoke\s+pretrial", "motion_revoke_bond", "critical", 0.30,
     "Motion to revoke bond filed"),
    (r"bond\s+(forfeited|estreated|forfeiture)", "bond_forfeiture", "critical", 0.50,
     "Bond forfeiture order entered"),
    (r"fugitive|absconded|fled\s+jurisdiction", "fugitive_status", "critical", 0.45,
     "Defendant declared fugitive"),
    # HIGH
    (r"sentenc(ed|ing)\s+(to|hearing|date)|sentence\s+imposed", "sentencing", "high", 0.15,
     "Sentencing event — case approaching disposition"),
    (r"guilty\s+(plea|verdict)|plead\s+guilty|nolo\s+contendere", "guilty_plea", "high", 0.10,
     "Guilty plea entered"),
    (r"probation\s+violat|vop\s+hearing|violation\s+of\s+probation", "probation_violation", "high", 0.20,
     "Probation violation hearing"),
    (r"bond\s+(increased|raised|modified\s+up)", "bond_increased", "high", 0.15,
     "Bond amount increased"),
    (r"arrest\s+warrant|new\s+warrant", "new_warrant", "high", 0.25,
     "New arrest warrant issued"),
    # MEDIUM
    (r"bond\s+(reduced|decreased|lowered)", "bond_reduced", "medium", -0.05,
     "Bond amount reduced — favorable"),
    (r"continu(ance|ed)|reset|postpone", "continuance", "medium", 0.05,
     "Case continued"),
    (r"motion\s+to\s+dismiss|nolle\s+prosequi|nol\s*pros", "motion_dismiss", "medium", -0.10,
     "Dismissal motion filed"),
    (r"pretrial\s+(conference|hearing|diversion)", "pretrial_hearing", "medium", 0.0,
     "Pretrial event scheduled"),
    (r"trial\s+(date|set|scheduled|begins)", "trial_scheduled", "medium", 0.05,
     "Trial date set"),
    # LOW / INFO
    (r"dismiss(ed|al)|case\s+closed|nolle\s+prosequi.*granted", "case_dismissed", "low", -0.20,
     "Case dismissed — bond discharge trigger"),
    (r"acquit|not\s+guilty", "acquittal", "low", -0.20,
     "Acquittal — bond discharge trigger"),
    (r"attorney|counsel\s+(appointed|retained)", "attorney_event", "info", 0.0,
     "Attorney of record changed"),
]

_COMPILED = [(re.compile(p, re.I), *rest) for p, *rest in DOCKET_EVENT_PATTERNS]
SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


def classify_docket_entry(text: str) -> list:
    """Classify a docket entry text. Returns list of (type, severity, risk_adj, desc)."""
    if not text:
        return []
    matches, seen = [], set()
    for regex, etype, sev, radj, desc in _COMPILED:
        if etype not in seen and regex.search(text):
            matches.append((etype, sev, radj, desc))
            seen.add(etype)
    matches.sort(key=lambda m: SEVERITY_RANK.get(m[1], 0), reverse=True)
    return matches


class DocketMonitor:
    """Async docket monitoring engine for active bonds."""

    def __init__(self, db, courtlistener_token: str = ""):
        self.db = db
        self._headers = {"User-Agent": "ShamrockLeads/1.0 (admin@shamrockbailbonds.biz)"}
        if courtlistener_token:
            self._headers["Authorization"] = f"Token {courtlistener_token}"
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url="https://www.courtlistener.com/api/rest/v4",
                headers=self._headers, timeout=30.0, follow_redirects=True,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def scan_active_bonds(self, limit: int = 100) -> dict:
        """Main scan: iterate active bonds, search dockets, classify, store."""
        start = datetime.utcnow()
        log.info("[DocketMonitor] Starting active bond docket scan...")
        try:
            cursor = self.db.active_bonds.find(
                {"status": {"$in": ["active", "monitoring", "alert"]}},
                {"Bond_Case_ID": 1, "defendant_name": 1, "Defendant_Name": 1,
                 "Defendant_ID": 1, "county": 1, "County": 1,
                 "case_number": 1, "bond_amount": 1, "Bond_Amount": 1, "status": 1},
            ).limit(limit)
            bonds = await cursor.to_list(length=limit)
        except Exception as e:
            return {"success": False, "error": str(e)}

        if not bonds:
            return {"success": True, "bonds_scanned": 0, "events_found": 0, "alerts_created": 0}

        total_events, total_alerts, bonds_with, errors = 0, 0, 0, 0
        for bond in bonds:
            try:
                name = bond.get("defendant_name") or bond.get("Defendant_Name") or ""
                if len(name) < 3:
                    continue
                events = await self._search_dockets(
                    name, bond.get("case_number"), bond.get("county") or bond.get("County"),
                )
                if events:
                    bonds_with += 1
                for ev in events:
                    ev.update({
                        "bond_case_id": str(bond.get("Bond_Case_ID") or bond.get("_id", "")),
                        "defendant_id": str(bond.get("Defendant_ID") or ""),
                        "defendant_name": name,
                        "bond_amount": float(bond.get("bond_amount") or bond.get("Bond_Amount") or 0),
                        "bond_status": bond.get("status", "active"),
                    })
                    if await self._store_event(ev):
                        total_events += 1
                        if ev.get("event_severity") in ("critical", "high"):
                            total_alerts += 1
                            await self._fire_alert(ev)
                await asyncio.sleep(1.0)
            except Exception as e:
                errors += 1
                log.warning("[DocketMonitor] Bond scan error: %s", str(e)[:100])

        dur = (datetime.utcnow() - start).total_seconds()
        log.info("[DocketMonitor] Done — %d bonds, %d events, %d alerts, %.1fs", len(bonds), total_events, total_alerts, dur)
        return {
            "success": True, "bonds_scanned": len(bonds), "bonds_with_events": bonds_with,
            "events_found": total_events, "alerts_created": total_alerts,
            "errors": errors, "duration_seconds": round(dur, 1),
            "scanned_at": datetime.utcnow().isoformat() + "Z",
        }

    async def _search_dockets(self, name: str, case_number: str = None, county: str = None) -> list:
        """Search CourtListener for docket entries matching a defendant."""
        events = []
        client = await self._get_client()
        # RECAP docket search
        try:
            params = {"type": "r", "q": f'"{name}"'}
            if county:
                params["court"] = "flsd flmd flnd"
            resp = await client.get("/search/", params=params)
            if resp.status_code == 200:
                for r in (resp.json().get("results") or [])[:10]:
                    events.extend(self._classify_result(r, name))
            elif resp.status_code == 429:
                await asyncio.sleep(5)
        except Exception as e:
            log.debug("Docket search error: %s", str(e)[:80])

        await asyncio.sleep(1.0)
        # Opinion search (90-day window)
        try:
            params = {"type": "o", "q": f'"{name}"',
                      "filed_after": (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d")}
            if county:
                params["court"] = "fla fladistctapp_2 flsd flmd"
            resp = await client.get("/search/", params=params)
            if resp.status_code == 200:
                for r in (resp.json().get("results") or [])[:5]:
                    events.extend(self._classify_result(r, name))
        except Exception:
            pass
        return events

    def _classify_result(self, result: dict, defendant_name: str) -> list:
        """Classify a CourtListener result into typed docket events."""
        text = " ".join(filter(None, [
            result.get("snippet", ""), result.get("caseName", "") or result.get("case_name", ""),
        ]))
        if not text:
            return []
        classifications = classify_docket_entry(text)
        if not classifications:
            return []
        sid = str(result.get("id", ""))
        dnum = result.get("docketNumber") or result.get("docket_number", "")
        cid = result.get("court_id", "")
        dfiled = result.get("dateFiled") or result.get("date_filed", "")
        cname = result.get("caseName") or result.get("case_name", "")
        from dashboard.services.courtlistener_client import SE_US_COURTS
        court_name = SE_US_COURTS.get(cid, {}).get("name", cid)
        out = []
        for etype, sev, radj, desc in classifications:
            out.append({
                "source": "courtlistener", "source_id": f"{sid}_{etype}",
                "docket_number": dnum, "court_id": cid, "court_name": court_name,
                "case_name": cname, "event_type": etype, "event_severity": sev,
                "event_date": dfiled, "description": desc, "raw_text": text[:500],
                "risk_adjustment": radj, "acknowledged": False,
                "acknowledged_by": None, "acknowledged_at": None,
                "detected_at": datetime.utcnow().isoformat() + "Z",
            })
        return out

    async def _store_event(self, event: dict) -> bool:
        """Store docket event, deduplicating by source_id + bond_case_id."""
        sid = event.get("source_id", "")
        bid = event.get("bond_case_id", "")
        if not sid:
            return False
        if await self.db.docket_events.find_one({"source_id": sid, "bond_case_id": bid}):
            return False
        await self.db.docket_events.insert_one(event)
        log.info("[DocketMonitor] New: %s [%s] %s", event.get("event_type"), event.get("event_severity"), event.get("defendant_name", "?"))
        return True

    async def _fire_alert(self, event: dict):
        """Slack + notification center alert for critical/high events."""
        webhook = os.getenv("SLACK_WEBHOOK_LEADS", "")
        if webhook:
            emoji = {"critical": "\U0001f6a8", "high": "\u26a0\ufe0f"}.get(event.get("event_severity"), "\U0001f4cb")
            ba = event.get("bond_amount", 0)
            msg = (f"{emoji} *DOCKET ALERT — {event.get('event_type','').upper().replace('_',' ')}*\n"
                   f"Defendant: *{event.get('defendant_name','?')}*\n"
                   f"Bond: ${ba:,.0f} | Status: {event.get('bond_status','active')}\n"
                   f"Court: {event.get('court_name','')} | {event.get('description','')}")
            try:
                async with httpx.AsyncClient(timeout=10) as c:
                    await c.post(webhook, json={"text": msg})
            except Exception:
                pass
        try:
            await self.db.notifications.insert_one({
                "type": "docket_alert", "severity": event.get("event_severity"),
                "title": f"Docket: {event.get('event_type','').replace('_',' ').title()}",
                "message": f"{event.get('defendant_name','')} — {event.get('description','')}",
                "bond_case_id": event.get("bond_case_id"), "read": False,
                "created_at": datetime.utcnow().isoformat() + "Z",
            })
        except Exception:
            pass

    # ── Query Methods ────────────────────────────────────────────────────
    async def get_recent_events(self, limit=50, severity=None, acknowledged=None) -> list:
        q = {}
        if severity:
            q["event_severity"] = severity
        if acknowledged is not None:
            q["acknowledged"] = acknowledged
        cur = self.db.docket_events.find(q).sort("detected_at", -1).limit(limit)
        evts = await cur.to_list(length=limit)
        for e in evts:
            e["_id"] = str(e["_id"])
        return evts

    async def get_bond_events(self, bond_case_id: str) -> list:
        cur = self.db.docket_events.find({"bond_case_id": bond_case_id}).sort("detected_at", -1)
        evts = await cur.to_list(length=100)
        for e in evts:
            e["_id"] = str(e["_id"])
        return evts

    async def get_alert_summary(self) -> dict:
        pipeline = [{"$match": {"acknowledged": False}}, {"$group": {"_id": "$event_severity", "count": {"$sum": 1}}}]
        results = await self.db.docket_events.aggregate(pipeline).to_list(length=10)
        summary = {r["_id"]: r["count"] for r in results}
        lc = await self.db.docket_events.find_one({"event_severity": "critical", "acknowledged": False}, sort=[("detected_at", -1)])
        if lc:
            lc["_id"] = str(lc["_id"])
        return {"total_unacknowledged": sum(summary.values()), "by_severity": summary, "latest_critical": lc}

    async def acknowledge_event(self, event_id: str, actor: str = "system") -> bool:
        from bson import ObjectId
        try:
            r = await self.db.docket_events.update_one(
                {"_id": ObjectId(event_id)},
                {"$set": {"acknowledged": True, "acknowledged_by": actor, "acknowledged_at": datetime.utcnow().isoformat() + "Z"}},
            )
            return r.modified_count > 0
        except Exception:
            return False

    async def get_monitoring_stats(self) -> dict:
        total = await self.db.docket_events.count_documents({})
        unack = await self.db.docket_events.count_documents({"acknowledged": False})
        crit = await self.db.docket_events.count_documents({"event_severity": "critical", "acknowledged": False})
        high = await self.db.docket_events.count_documents({"event_severity": "high", "acknowledged": False})
        active = await self.db.active_bonds.count_documents({"status": {"$in": ["active", "monitoring", "alert"]}})
        last = await self.db.docket_events.find_one({}, {"detected_at": 1}, sort=[("detected_at", -1)])
        tp = [{"$group": {"_id": "$event_type", "count": {"$sum": 1}}}, {"$sort": {"count": -1}}, {"$limit": 10}]
        by_type = await self.db.docket_events.aggregate(tp).to_list(length=10)
        week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat() + "Z"
        weekly = await self.db.docket_events.count_documents({"detected_at": {"$gte": week_ago}})
        return {
            "success": True, "total_events": total, "unacknowledged": unack,
            "critical_alerts": crit, "high_alerts": high,
            "active_bonds_monitored": active, "events_last_7d": weekly,
            "last_scan": last.get("detected_at") if last else None,
            "by_event_type": [{"type": t["_id"], "count": t["count"]} for t in by_type],
        }


async def run_docket_scan(db, limit: int = 100) -> dict:
    """Convenience wrapper for the background cron loop."""
    token = os.getenv("COURTLISTENER_API_TOKEN", "")
    monitor = DocketMonitor(db, courtlistener_token=token)
    try:
        return await monitor.scan_active_bonds(limit=limit)
    finally:
        await monitor.close()
