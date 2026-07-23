"""
OSINT Intelligence Service v2 — ShamrockLeads
==============================================
Multi-engine orchestration: Maigret · Sherlock · Blackbird · SpiderFoot
Admin-only. Audit + Mongo storage + HTTP client to osint-worker.

Security Rules:
  - All scan invocations are audited via AuditService.
  - PII must NOT appear in application logs.
  - Results stored in `osint_scans` collection.
  - OSINT risk delta is advisory only (never auto-applied to bond risk).
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from bson import ObjectId

from dashboard.extensions import get_db
from dashboard.models.osint import (
    FindingRelevanceUpdate,
    OSINTScanRequest,
)
from dashboard.services.audit_service import AuditService

log = logging.getLogger("shamrock.osint")

OSINT_WORKER_URL = os.getenv("OSINT_WORKER_URL", "http://osint-worker:5065").rstrip("/")
OSINT_WORKER_KEY = os.getenv("OSINT_WORKER_KEY", "").strip()
OSINT_WORKER_TIMEOUT = float(os.getenv("OSINT_WORKER_TIMEOUT", "600"))


def _redact(value: Optional[str]) -> str:
    if not value:
        return "[empty]"
    h = hashlib.sha256(value.encode()).hexdigest()[:8]
    return f"[redacted:{h}]"


def _worker_headers() -> Dict[str, str]:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if OSINT_WORKER_KEY:
        h["X-Worker-Key"] = OSINT_WORKER_KEY
    return h


class OSINTService:
    """Orchestrates multi-engine OSINT scans via worker + manages report storage."""

    def __init__(self):
        self._db = None

    def _get_db(self):
        if self._db is None:
            self._db = get_db()
        return self._db

    @property
    def _scans_col(self):
        return self._get_db()["osint_scans"]

    @property
    def _trape_col(self):
        return self._get_db()["osint_trape_sessions"]

    # ── Tool Probe ────────────────────────────────────────────────────────────

    @staticmethod
    def probe_tools() -> Dict[str, Any]:
        """Synchronous tool availability probe via worker."""
        trape_dir = os.getenv("TRAPE_DIR", "/opt/trape")
        trape_path = os.path.join(trape_dir, "trape.py")
        trape_server = os.getenv("TRAPE_SERVER_URL", "")
        trape = {
            "available": os.path.isfile(trape_path),
            "server_url": trape_server or "not configured",
        }

        try:
            with httpx.Client(timeout=8.0) as client:
                r = client.get(f"{OSINT_WORKER_URL}/status", headers=_worker_headers())
                if r.status_code == 200:
                    data = r.json()
                    data["trape"] = trape
                    data["worker_url"] = OSINT_WORKER_URL
                    data["worker_reachable"] = True
                    data["ready_for_scans"] = bool(data.get("ready_for_scans"))
                    return data
                return {
                    "maigret": {"available": False},
                    "sherlock": {"available": False},
                    "blackbird": {"available": False},
                    "spiderfoot": {"available": False},
                    "trape": trape,
                    "ready_for_scans": False,
                    "worker_reachable": False,
                    "worker_url": OSINT_WORKER_URL,
                    "error": f"worker HTTP {r.status_code}",
                }
        except Exception as exc:
            log.warning("OSINT worker probe failed: %s", exc)
            return {
                "maigret": {"available": False},
                "sherlock": {"available": False},
                "blackbird": {"available": False},
                "spiderfoot": {"available": False},
                "trape": trape,
                "ready_for_scans": False,
                "worker_reachable": False,
                "worker_url": OSINT_WORKER_URL,
                "error": str(exc)[:300],
            }

    async def get_queue_info(self) -> Dict[str, Any]:
        """Get current queue depth (running/queued scans)."""
        col = self._scans_col
        running = await col.count_documents({"status": {"$in": ["running", "queued"]}})
        total = await col.count_documents({})
        return {"running": running, "total_scans": total}

    # ── Scan Orchestration ────────────────────────────────────────────────────

    async def run_scan(self, req: OSINTScanRequest, actor: str = "admin") -> str:
        """Insert scan doc, dispatch background worker, return scan_id."""
        col = self._scans_col
        now = datetime.now(timezone.utc)

        engines_list = [e.value for e in req.engines]

        # Build progress dict
        progress = {}
        for engine in engines_list:
            progress[engine] = {
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "accounts_found": 0,
                "entities_found": 0,
                "error": None,
                "warning": None,
            }

        doc = {
            "subject_type": req.subject_type.value,
            "subject_id": req.subject_id,
            "full_name": req.full_name,
            "scan_requested_by": actor,
            "engines_requested": engines_list,
            "scan_params": {
                "usernames": req.usernames or [],
                "email": req.email,
                "phone": req.phone,
                "full_name": req.full_name,
                "deep_scan": req.deep_scan,
                "second_opinion": req.second_opinion,
            },
            "status": "running",
            "progress": progress,
            "accounts": [],
            "entities": [],
            "total_accounts": 0,
            "total_entities": 0,
            "platforms_found": [],
            "risk_signals": [],
            "osint_risk_score": 0,
            "risk_is_advisory": True,
            "ai_summary": None,
            "raw_outputs": {},
            "tool_results": {},
            "warnings": [],
            "error": None,
            "notes": req.notes,
            "created_at": now,
            "started_at": now,
            "completed_at": None,
        }

        result = await col.insert_one(doc)
        scan_id = str(result.inserted_id)

        await AuditService.log_event(
            entity_type=req.subject_type.value,
            entity_id=req.subject_id,
            action="osint_scan_initiated",
            details={
                "scan_id": scan_id,
                "engines": engines_list,
                "deep_scan": req.deep_scan,
            },
            actor=actor,
            actor_type="admin",
            event_context="osint_intelligence",
        )

        # Dispatch background execution
        asyncio.create_task(self._execute_scan(scan_id, req, actor))
        return scan_id

    async def _execute_scan(
        self, scan_id: str, req: OSINTScanRequest, actor: str
    ) -> None:
        """Background task: call worker and update Mongo with results."""
        col = self._scans_col
        error_msg: Optional[str] = None
        worker_result: Dict[str, Any] = {}

        try:
            payload = {
                "usernames": req.usernames or [],
                "full_name": req.full_name,
                "email": req.email,
                "phone": req.phone,
                "deep_scan": req.deep_scan,
                "engines": [e.value for e in req.engines],
                "second_opinion": req.second_opinion,
            }
            async with httpx.AsyncClient(timeout=OSINT_WORKER_TIMEOUT) as client:
                r = await client.post(
                    f"{OSINT_WORKER_URL}/v2/scan",
                    headers=_worker_headers(),
                    json=payload,
                )
                if r.status_code >= 400:
                    detail = r.text[:400]
                    try:
                        detail = r.json().get("detail", detail)
                    except Exception:
                        pass
                    raise RuntimeError(f"Worker scan failed HTTP {r.status_code}: {detail}")
                worker_result = r.json()
        except Exception as exc:
            log.error("OSINT worker scan %s failed: %s", scan_id, exc)
            error_msg = str(exc)
            worker_result = {
                "status": "failed",
                "accounts": [],
                "entities": [],
                "total_accounts": 0,
                "total_entities": 0,
                "platforms_found": [],
                "risk_signals": [{
                    "signal_type": "osint_scan_failed",
                    "severity": "high",
                    "detail": error_msg,
                    "source": "osint_service",
                }],
                "osint_risk_score": 0,
                "progress": {},
                "raw_outputs": {},
                "tool_results": {},
                "warnings": [],
                "error": error_msg,
            }

        status = worker_result.get("status") or ("failed" if error_msg else "completed")
        now = datetime.now(timezone.utc)

        update = {
            "$set": {
                "status": status,
                "completed_at": now,
                "progress": worker_result.get("progress") or {},
                "accounts": worker_result.get("accounts") or [],
                "entities": worker_result.get("entities") or [],
                "total_accounts": int(worker_result.get("total_accounts") or 0),
                "total_entities": int(worker_result.get("total_entities") or 0),
                "platforms_found": worker_result.get("platforms_found") or [],
                "risk_signals": worker_result.get("risk_signals") or [],
                "osint_risk_score": int(worker_result.get("osint_risk_score") or 0),
                "risk_is_advisory": True,
                "ai_summary": worker_result.get("ai_summary"),
                "raw_outputs": worker_result.get("raw_outputs") or {},
                "tool_results": worker_result.get("tool_results") or {},
                "warnings": worker_result.get("warnings") or [],
                "error": worker_result.get("error") or error_msg,
            }
        }
        await col.update_one({"_id": ObjectId(scan_id)}, update)

        await AuditService.log_event(
            entity_type=req.subject_type.value,
            entity_id=req.subject_id,
            action="osint_scan_complete",
            details={
                "scan_id": scan_id,
                "accounts_found": int(worker_result.get("total_accounts") or 0),
                "entities_found": int(worker_result.get("total_entities") or 0),
                "status": status,
            },
            actor=actor,
            actor_type="admin",
            event_context="osint_intelligence",
        )

        log.info(
            "OSINT scan %s %s — %s accounts, %s entities",
            scan_id, status,
            worker_result.get("total_accounts"),
            worker_result.get("total_entities"),
        )

    # ── Scan Retrieval ────────────────────────────────────────────────────────

    async def get_scan(self, scan_id: str, include_raw: bool = False) -> Optional[Dict]:
        try:
            projection = None if include_raw else {"raw_outputs": 0}
            doc = await self._scans_col.find_one(
                {"_id": ObjectId(scan_id)}, projection
            )
        except Exception:
            return None
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        return doc

    async def list_scans(
        self,
        subject_id: Optional[str] = None,
        subject_type: Optional[str] = None,
        status: Optional[str] = None,
        engine: Optional[str] = None,
        search: Optional[str] = None,
        sort: str = "newest",
        limit: int = 25,
        skip: int = 0,
    ) -> Tuple[List[Dict], int]:
        query: Dict[str, Any] = {}
        if subject_id:
            query["subject_id"] = subject_id
        if subject_type:
            query["subject_type"] = subject_type
        if status:
            query["status"] = status
        if engine:
            query["engines_requested"] = engine
        if search:
            query["full_name"] = {"$regex": search, "$options": "i"}

        sort_key = [("created_at", -1)]
        if sort == "oldest":
            sort_key = [("created_at", 1)]
        elif sort == "accounts":
            sort_key = [("total_accounts", -1)]

        total = await self._scans_col.count_documents(query)
        cursor = self._scans_col.find(
            query, {"raw_outputs": 0}
        ).sort(sort_key).skip(skip).limit(limit)

        docs = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            docs.append(doc)
        return docs, total

    # ── Finding Management ────────────────────────────────────────────────────

    async def update_findings_relevance(
        self, scan_id: str, body: FindingRelevanceUpdate
    ) -> bool:
        try:
            doc = await self._scans_col.find_one({"_id": ObjectId(scan_id)})
        except Exception:
            return False
        if not doc:
            return False

        accounts = doc.get("accounts") or []
        entities = doc.get("entities") or []

        for idx in (body.account_indices or []):
            if 0 <= idx < len(accounts):
                accounts[idx]["relevance"] = body.relevance.value

        for idx in (body.entity_indices or []):
            if 0 <= idx < len(entities):
                entities[idx]["relevance"] = body.relevance.value

        await self._scans_col.update_one(
            {"_id": ObjectId(scan_id)},
            {"$set": {"accounts": accounts, "entities": entities}},
        )
        return True

    async def attach_to_subject(self, scan_id: str, actor: str = "admin") -> Optional[Dict]:
        """Write OSINT summary into the subject's record."""
        try:
            doc = await self._scans_col.find_one({"_id": ObjectId(scan_id)})
        except Exception:
            return None
        if not doc:
            return None

        subject_type = doc.get("subject_type", "defendant")
        subject_id = doc.get("subject_id")
        collection_name = f"{subject_type}s"

        summary = {
            "osint_scan_id": scan_id,
            "osint_date": doc.get("completed_at") or doc.get("created_at"),
            "osint_engines": doc.get("engines_requested", []),
            "osint_accounts_found": doc.get("total_accounts", 0),
            "osint_entities_found": doc.get("total_entities", 0),
            "osint_risk_score": doc.get("osint_risk_score", 0),
            "osint_risk_advisory": True,
            "osint_platforms": doc.get("platforms_found", []),
            "osint_summary": doc.get("ai_summary") or f"{doc.get('total_accounts', 0)} accounts found across {len(doc.get('platforms_found', []))} platforms",
        }

        db = self._get_db()
        try:
            result = await db[collection_name].update_one(
                {"_id": ObjectId(subject_id)},
                {"$set": {"osint_intel": summary}},
            )
        except Exception as exc:
            log.error("Failed to attach OSINT to %s/%s: %s", collection_name, subject_id, exc)
            return {"success": False, "error": str(exc)}

        await AuditService.log_event(
            entity_type=subject_type,
            entity_id=subject_id,
            action="osint_attached_to_subject",
            details={"scan_id": scan_id, "accounts": doc.get("total_accounts", 0)},
            actor=actor,
            actor_type="admin",
            event_context="osint_intelligence",
        )

        return {
            "success": bool(result.modified_count),
            "subject_type": subject_type,
            "subject_id": subject_id,
            "summary": summary,
        }

    # ── Export Methods ────────────────────────────────────────────────────────

    async def export_json(self, scan_id: str) -> Optional[Dict]:
        """Full structured JSON export."""
        doc = await self.get_scan(scan_id, include_raw=True)
        if not doc:
            return None
        # Clean for export
        doc.pop("_id", None)
        doc["export_type"] = "osint_full_report"
        doc["exported_at"] = datetime.now(timezone.utc).isoformat()
        return doc

    async def export_csv(self, scan_id: str) -> Optional[str]:
        """Flat CSV of accounts + entities."""
        doc = await self.get_scan(scan_id, include_raw=False)
        if not doc:
            return None

        output = io.StringIO()
        writer = csv.writer(output)

        # Accounts section
        writer.writerow([
            "Type", "Platform", "URL", "Username", "Source",
            "Confidence", "Category", "Relevance",
        ])
        for acct in doc.get("accounts") or []:
            writer.writerow([
                "account",
                acct.get("platform", ""),
                acct.get("url", ""),
                acct.get("username", ""),
                acct.get("source", ""),
                acct.get("confidence", ""),
                acct.get("category", ""),
                acct.get("relevance", "unreviewed"),
            ])

        # Entities section
        writer.writerow([])
        writer.writerow([
            "Type", "Entity Type", "Value", "Source",
            "Module", "Confidence", "Context", "Relevance",
        ])
        for ent in doc.get("entities") or []:
            writer.writerow([
                "entity",
                ent.get("type", ""),
                ent.get("value", ""),
                ent.get("source", ""),
                ent.get("module", ""),
                ent.get("confidence", ""),
                ent.get("context", ""),
                ent.get("relevance", "unreviewed"),
            ])

        return output.getvalue()

    async def export_pdf(self, scan_id: str) -> Optional[bytes]:
        """Generate a PDF summary report."""
        doc = await self.get_scan(scan_id, include_raw=False)
        if not doc:
            return None

        try:
            from dashboard.services.osint_pdf_export import generate_osint_pdf
            return generate_osint_pdf(doc)
        except ImportError:
            # Fallback: simple text-based PDF
            return self._fallback_pdf(doc)

    def _fallback_pdf(self, doc: Dict) -> bytes:
        """Simple fallback PDF using fpdf2."""
        try:
            from fpdf import FPDF
        except ImportError:
            return b""

        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, "OSINT Intelligence Report", ln=True, align="C")
        pdf.ln(5)

        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, f"Subject: {doc.get('full_name', 'Unknown')} ({doc.get('subject_type', '')})", ln=True)
        pdf.cell(0, 6, f"Engines: {', '.join(doc.get('engines_requested', []))}", ln=True)
        pdf.cell(0, 6, f"Status: {doc.get('status', '')}", ln=True)
        pdf.cell(0, 6, f"Accounts Found: {doc.get('total_accounts', 0)}", ln=True)
        pdf.cell(0, 6, f"Entities Found: {doc.get('total_entities', 0)}", ln=True)
        pdf.cell(0, 6, f"Risk Score: {doc.get('osint_risk_score', 0)} (advisory)", ln=True)
        pdf.cell(0, 6, f"Date: {doc.get('created_at', '')}", ln=True)
        pdf.ln(8)

        # Accounts table
        if doc.get("accounts"):
            pdf.set_font("Helvetica", "B", 12)
            pdf.cell(0, 8, "Discovered Accounts", ln=True)
            pdf.set_font("Helvetica", "", 9)
            for acct in doc["accounts"][:50]:  # Cap at 50 for PDF
                line = f"  {acct.get('platform', '?')} | {acct.get('username', '')} | {acct.get('url', '')[:60]} | {acct.get('source', '')}"
                pdf.cell(0, 5, line, ln=True)
            if len(doc["accounts"]) > 50:
                pdf.cell(0, 5, f"  ... and {len(doc['accounts']) - 50} more", ln=True)

        # Entities
        if doc.get("entities"):
            pdf.ln(5)
            pdf.set_font("Helvetica", "B", 12)
            pdf.cell(0, 8, "Discovered Entities", ln=True)
            pdf.set_font("Helvetica", "", 9)
            for ent in doc["entities"][:30]:
                line = f"  [{ent.get('type', '?')}] {ent.get('value', '')} (via {ent.get('source', '')})"
                pdf.cell(0, 5, line, ln=True)

        # Risk signals
        if doc.get("risk_signals"):
            pdf.ln(5)
            pdf.set_font("Helvetica", "B", 12)
            pdf.cell(0, 8, "Risk Signals", ln=True)
            pdf.set_font("Helvetica", "", 9)
            for sig in doc["risk_signals"]:
                line = f"  [{sig.get('severity', '').upper()}] {sig.get('signal_type', '')}: {sig.get('detail', '')[:80]}"
                pdf.cell(0, 5, line, ln=True)

        pdf.ln(10)
        pdf.set_font("Helvetica", "I", 8)
        pdf.cell(0, 5, "Generated by ShamrockLeads OSINT Intelligence Module. Advisory use only.", ln=True)

        return pdf.output()

    # ── Trape Session Management (unchanged) ──────────────────────────────────

    async def create_trape_session(
        self,
        subject_type: str,
        subject_id: str,
        lure_url: str,
        notes: Optional[str] = None,
        actor: str = "admin",
    ) -> Dict:
        session_id = secrets.token_urlsafe(16)
        now = datetime.now(timezone.utc)
        trape_server = os.getenv("TRAPE_SERVER_URL", "")
        tracking_url = f"{trape_server}/track/{session_id}" if trape_server else None
        doc = {
            "subject_type": subject_type,
            "subject_id": subject_id,
            "session_id": session_id,
            "lure_url": lure_url,
            "tracking_url": tracking_url,
            "created_at": now,
            "status": "active",
            "ip_address": None,
            "geolocation": None,
            "device_info": None,
            "session_tokens": [],
            "notes": notes,
            "trape_command": (
                f"python3 {os.path.join(os.getenv('TRAPE_DIR', '/opt/trape'), 'trape.py')} "
                f"--url {lure_url} --port 8099 --accesskey {session_id}"
            ),
        }
        result = await self._trape_col.insert_one(doc)
        doc["_id"] = str(result.inserted_id)
        await AuditService.log_event(
            entity_type=subject_type,
            entity_id=subject_id,
            action="trape_session_created",
            details={"session_id": session_id, "lure_url": lure_url},
            actor=actor,
            actor_type="admin",
            event_context="osint_intelligence",
        )
        return doc

    async def update_trape_session(
        self, session_id: str, data: Dict, actor: str = "admin"
    ) -> bool:
        result = await self._trape_col.update_one(
            {"session_id": session_id},
            {"$set": {**data, "status": "triggered", "updated_at": datetime.now(timezone.utc)}},
        )
        if result.modified_count:
            await AuditService.log_event(
                entity_type="trape_session",
                entity_id=session_id,
                action="trape_data_received",
                details={"fields_updated": list(data.keys())},
                actor=actor,
                actor_type="admin",
                event_context="osint_intelligence",
            )
        return result.modified_count > 0

    async def list_trape_sessions(
        self, subject_id: Optional[str] = None, limit: int = 20
    ) -> List[Dict]:
        query: Dict = {}
        if subject_id:
            query["subject_id"] = subject_id
        docs = []
        cursor = self._trape_col.find(query).sort("created_at", -1).limit(limit)
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            docs.append(doc)
        return docs


_service: Optional[OSINTService] = None


def get_osint_service() -> OSINTService:
    global _service
    if _service is None:
        _service = OSINTService()
    return _service
