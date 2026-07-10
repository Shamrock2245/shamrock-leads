"""
OSINT Intelligence Service — ShamrockLeads
==========================================
Admin-only orchestration: audit + Mongo storage + HTTP client to osint-worker.

CLI tools (Maigret / Blackbird) run in the dedicated osint-worker container
(writable FS). This service never shells out to those tools when
OSINT_WORKER_URL is configured.

Security Rules:
  - All scan invocations are audited via AuditService.
  - PII must NOT appear in application logs.
  - Results stored in `osint_profiles`.
  - OSINT risk delta is advisory only (never auto-applied to bond risk).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from bson import ObjectId

from dashboard.extensions import get_db
from dashboard.services.audit_service import AuditService

log = logging.getLogger("shamrock.osint")

OSINT_WORKER_URL = os.getenv("OSINT_WORKER_URL", "http://osint-worker:5065").rstrip("/")
OSINT_WORKER_KEY = os.getenv("OSINT_WORKER_KEY", "").strip()
# Long timeout — worker runs Maigret synchronously
OSINT_WORKER_TIMEOUT = float(os.getenv("OSINT_WORKER_TIMEOUT", "300"))


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
    """Orchestrates OSINT scans via worker + manages report storage."""

    def __init__(self):
        self._db = None

    def _get_db(self):
        if self._db is None:
            self._db = get_db()
        return self._db

    @property
    def _collection(self):
        return self._get_db()["osint_profiles"]

    @property
    def _trape_col(self):
        return self._get_db()["osint_trape_sessions"]

    # ── Tool probe (proxy to worker) ──────────────────────────────────────────

    @staticmethod
    def probe_tools() -> Dict[str, Any]:
        """
        Synchronous tool availability probe via worker (safe for status API).
        Falls back to a clear offline payload if worker unreachable.
        """
        trape_dir = os.getenv("TRAPE_DIR", "/opt/trape")
        trape_path = os.path.join(trape_dir, "trape.py")
        trape_server = os.getenv("TRAPE_SERVER_URL", "")
        trape = {
            "available": os.path.isfile(trape_path),
            "path": trape_path if os.path.isfile(trape_path) else "not found",
            "server_url": trape_server or "not configured — set TRAPE_SERVER_URL",
            "note": (
                "Trape requires a separate public server. "
                "Use /api/osint/trape/session for operator payloads only."
            ),
        }

        try:
            with httpx.Client(timeout=8.0) as client:
                r = client.get(
                    f"{OSINT_WORKER_URL}/status",
                    headers=_worker_headers(),
                )
                if r.status_code == 200:
                    data = r.json()
                    data["trape"] = trape
                    data["worker_url"] = OSINT_WORKER_URL
                    data["worker_reachable"] = True
                    data["ready_for_scans"] = bool(data.get("ready_for_scans"))
                    # Surface policy defaults
                    data.setdefault("defaults", {
                        "maigret_default": True,
                        "blackbird_default": False,
                        "blackbird_on_email": True,
                        "blackbird_on_second_opinion": True,
                    })
                    return data
                return {
                    "maigret": {"available": False, "path": "worker error", "error": f"HTTP {r.status_code}"},
                    "blackbird": {"available": False, "path": "worker error", "error": f"HTTP {r.status_code}"},
                    "trape": trape,
                    "ready_for_scans": False,
                    "worker_url": OSINT_WORKER_URL,
                    "worker_reachable": False,
                    "error": f"worker status HTTP {r.status_code}",
                }
        except Exception as exc:
            log.warning("OSINT worker probe failed: %s", exc)
            return {
                "maigret": {
                    "available": False,
                    "path": "worker offline",
                    "error": f"osint-worker unreachable at {OSINT_WORKER_URL}: {exc}",
                },
                "blackbird": {
                    "available": False,
                    "path": "worker offline",
                    "error": f"osint-worker unreachable: {exc}",
                },
                "trape": trape,
                "ready_for_scans": False,
                "worker_url": OSINT_WORKER_URL,
                "worker_reachable": False,
                "error": str(exc)[:300],
            }

    async def _call_worker_scan(
        self,
        *,
        usernames: Optional[List[str]],
        full_name: Optional[str],
        email: Optional[str],
        deep_scan: bool,
        run_maigret: Optional[bool],
        run_blackbird: Optional[bool],
        second_opinion: bool,
    ) -> Dict[str, Any]:
        payload = {
            "usernames": usernames or [],
            "full_name": full_name,
            "email": email,
            "deep_scan": deep_scan,
            "run_maigret": run_maigret,
            "run_blackbird": run_blackbird,
            "second_opinion": second_opinion,
        }
        async with httpx.AsyncClient(timeout=OSINT_WORKER_TIMEOUT) as client:
            r = await client.post(
                f"{OSINT_WORKER_URL}/v1/scan",
                headers=_worker_headers(),
                json=payload,
            )
            if r.status_code >= 400:
                detail = r.text[:400]
                try:
                    detail = r.json().get("detail", detail)
                except Exception:
                    pass
                raise RuntimeError(f"osint-worker scan failed HTTP {r.status_code}: {detail}")
            return r.json()

    # ── Main Scan Orchestrator ─────────────────────────────────────────────────

    async def run_scan(
        self,
        subject_type: str,
        subject_id: str,
        full_name: Optional[str],
        usernames: Optional[List[str]],
        email: Optional[str],
        deep_scan: bool = False,
        run_maigret: Optional[bool] = None,
        run_blackbird: Optional[bool] = None,
        second_opinion: bool = False,
        notes: Optional[str] = None,
        actor: str = "admin",
    ) -> str:
        """
        Insert pending report, dispatch background worker scan, return report_id.
        Policy defaults applied on the worker when run_* is None.
        """
        col = self._collection
        now = datetime.now(timezone.utc)

        doc = {
            "subject_type": subject_type,
            "subject_id": subject_id,
            "full_name": full_name,
            "scan_requested_by": actor,
            "scan_started_at": now,
            "scan_completed_at": None,
            "status": "running",
            "run_maigret": run_maigret,
            "run_blackbird": run_blackbird,
            "second_opinion": second_opinion,
            "deep_scan": deep_scan,
            "maigret_accounts": [],
            "blackbird_accounts": [],
            "total_accounts_found": 0,
            "platforms_found": [],
            "risk_signals": [],
            "osint_risk_score": 0,
            "risk_is_advisory": True,
            "ai_summary": None,
            "raw_maigret_json": None,
            "raw_blackbird_json": None,
            "tool_results": {},
            "warnings": [],
            "error": None,
            "notes": notes,
            "worker_url": OSINT_WORKER_URL,
            "created_at": now,
        }
        result = await col.insert_one(doc)
        report_id = str(result.inserted_id)

        await AuditService.log_event(
            entity_type=subject_type,
            entity_id=subject_id,
            action="osint_scan_initiated",
            details={
                "report_id": report_id,
                "tools": {
                    "maigret": run_maigret,
                    "blackbird": run_blackbird,
                    "second_opinion": second_opinion,
                },
                "deep_scan": deep_scan,
                "worker": OSINT_WORKER_URL,
            },
            actor=actor,
            actor_type="admin",
            event_context="osint_intelligence",
        )

        asyncio.create_task(
            self._execute_scan(
                report_id=report_id,
                subject_type=subject_type,
                subject_id=subject_id,
                full_name=full_name,
                usernames=usernames or [],
                email=email,
                deep_scan=deep_scan,
                run_maigret=run_maigret,
                run_blackbird=run_blackbird,
                second_opinion=second_opinion,
                actor=actor,
            )
        )
        return report_id

    async def _execute_scan(
        self,
        report_id: str,
        subject_type: str,
        subject_id: str,
        full_name: Optional[str],
        usernames: List[str],
        email: Optional[str],
        deep_scan: bool,
        run_maigret: Optional[bool],
        run_blackbird: Optional[bool],
        second_opinion: bool,
        actor: str,
    ) -> None:
        col = self._collection
        error_msg: Optional[str] = None
        worker_result: Dict[str, Any] = {}

        try:
            worker_result = await self._call_worker_scan(
                usernames=usernames,
                full_name=full_name,
                email=email,
                deep_scan=deep_scan,
                run_maigret=run_maigret,
                run_blackbird=run_blackbird,
                second_opinion=second_opinion,
            )
        except Exception as exc:
            log.error("OSINT worker scan %s failed: %s", report_id, exc)
            error_msg = str(exc)
            worker_result = {
                "status": "failed",
                "maigret_accounts": [],
                "blackbird_accounts": [],
                "total_accounts_found": 0,
                "platforms_found": [],
                "risk_signals": [{
                    "signal_type": "osint_worker_unreachable",
                    "severity": "high",
                    "detail": error_msg,
                    "source": "osint_service",
                }],
                "osint_risk_score": 0,
                "tool_results": {},
                "warnings": [],
                "error": error_msg,
                "risk_is_advisory": True,
            }

        status = worker_result.get("status") or ("failed" if error_msg else "complete")
        if error_msg and status == "complete":
            status = "failed"

        now = datetime.now(timezone.utc)
        update = {
            "$set": {
                "status": status,
                "scan_completed_at": now,
                "maigret_accounts": worker_result.get("maigret_accounts") or [],
                "blackbird_accounts": worker_result.get("blackbird_accounts") or [],
                "total_accounts_found": int(worker_result.get("total_accounts_found") or 0),
                "platforms_found": worker_result.get("platforms_found") or [],
                "risk_signals": worker_result.get("risk_signals") or [],
                "osint_risk_score": int(worker_result.get("osint_risk_score") or 0),
                "risk_is_advisory": True,
                "ai_summary": worker_result.get("ai_summary"),
                "raw_maigret_json": worker_result.get("raw_maigret_json"),
                "raw_blackbird_json": worker_result.get("raw_blackbird_json"),
                "tool_results": worker_result.get("tool_results") or {},
                "warnings": worker_result.get("warnings") or [],
                "error": worker_result.get("error") or error_msg,
                "policy": worker_result.get("policy"),
                "full_name": full_name,
            }
        }
        await col.update_one({"_id": ObjectId(report_id)}, update)

        await AuditService.log_event(
            entity_type=subject_type,
            entity_id=subject_id,
            action="osint_scan_complete",
            details={
                "report_id": report_id,
                "accounts_found": int(worker_result.get("total_accounts_found") or 0),
                "risk_score_delta": int(worker_result.get("osint_risk_score") or 0),
                "status": status,
                "risk_is_advisory": True,
            },
            actor=actor,
            actor_type="admin",
            event_context="osint_intelligence",
        )

        log.info(
            "OSINT scan %s %s — %s accounts (advisory score +%s)",
            report_id,
            status,
            worker_result.get("total_accounts_found"),
            worker_result.get("osint_risk_score"),
        )

    # ── Report Retrieval ──────────────────────────────────────────────────────

    async def get_report(self, report_id: str) -> Optional[Dict]:
        try:
            doc = await self._collection.find_one({"_id": ObjectId(report_id)})
        except Exception:
            return None
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        doc.pop("raw_maigret_json", None)
        doc.pop("raw_blackbird_json", None)
        return doc

    async def get_raw_report(self, report_id: str) -> Optional[Dict]:
        try:
            doc = await self._collection.find_one({"_id": ObjectId(report_id)})
        except Exception:
            return None
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        return doc

    async def list_reports(
        self,
        subject_id: Optional[str] = None,
        subject_type: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict]:
        query: Dict = {}
        if subject_id:
            query["subject_id"] = subject_id
        if subject_type:
            query["subject_type"] = subject_type
        docs = []
        cursor = self._collection.find(query, {"raw_maigret_json": 0, "raw_blackbird_json": 0})
        cursor = cursor.sort("created_at", -1).limit(limit)
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            docs.append(doc)
        return docs

    # ── Trape Session Management ───────────────────────────────────────────────

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
