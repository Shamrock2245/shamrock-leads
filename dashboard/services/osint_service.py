"""
OSINT Intelligence Service — ShamrockLeads
==========================================
Admin-only service that orchestrates Maigret and Blackbird CLI tools to
build deep background intelligence on defendants and indemnitors.

Security Rules:
  - All scan invocations are audited via AuditService.
  - PII (names, DOBs, addresses) must NOT appear in application logs.
  - Results are stored in the `osint_profiles` MongoDB collection.
  - Only admin-authenticated requests may invoke this service.

Tool Integration:
  - Maigret: pip install maigret — username search across 3000+ sites.
  - Blackbird: cloned to /opt/blackbird — username/email search.
  - Trape: payload generation only (active tracking requires manual setup).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId

from dashboard.extensions import get_db
from dashboard.services.audit_service import AuditService

log = logging.getLogger("shamrock.osint")

# ── Tool paths ─────────────────────────────────────────────────────────────────
MAIGRET_CMD = shutil.which("maigret") or os.getenv("MAIGRET_PATH", "maigret")
BLACKBIRD_DIR = os.getenv("BLACKBIRD_DIR", "/opt/blackbird")
BLACKBIRD_CMD = os.path.join(BLACKBIRD_DIR, "blackbird.py")
PYTHON_CMD = shutil.which("python3") or "python3"

# Timeout for each tool (seconds)
MAIGRET_TIMEOUT = int(os.getenv("OSINT_MAIGRET_TIMEOUT", "120"))
BLACKBIRD_TIMEOUT = int(os.getenv("OSINT_BLACKBIRD_TIMEOUT", "60"))

# Admin key for extra-layer access control
OSINT_ADMIN_KEY = os.getenv("OSINT_ADMIN_KEY", "")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _redact(value: Optional[str]) -> str:
    """Return a SHA-256 prefix for log-safe PII references."""
    if not value:
        return "[empty]"
    h = hashlib.sha256(value.encode()).hexdigest()[:8]
    return f"[redacted:{h}]"


def _score_signals(accounts: List[Dict], subject_type: str) -> tuple[int, List[Dict]]:
    """
    Derive risk signals and a 0-100 OSINT risk score from discovered accounts.
    Returns (score_delta, signals_list).
    """
    signals: List[Dict] = []
    score = 0

    total = len(accounts)

    # High account count suggests multiple identities or evasion
    if total >= 30:
        score += 20
        signals.append({
            "signal_type": "high_account_count",
            "severity": "high",
            "detail": f"{total} accounts found across platforms — possible multiple identities.",
            "source": "osint_engine",
        })
    elif total >= 15:
        score += 10
        signals.append({
            "signal_type": "high_account_count",
            "severity": "medium",
            "detail": f"{total} accounts found — above-average digital footprint.",
            "source": "osint_engine",
        })

    # Check for out-of-state platform signals
    platforms = {a.get("platform", "").lower() for a in accounts}
    out_of_state_indicators = {"craigslist", "nextdoor", "meetup"}
    found_oos = platforms & out_of_state_indicators
    if found_oos:
        score += 8
        signals.append({
            "signal_type": "out_of_state",
            "severity": "medium",
            "detail": f"Active on platforms suggesting possible relocation: {', '.join(found_oos)}.",
            "source": "osint_engine",
        })

    # Criminal or legal mentions in profile data
    legal_keywords = {"arrest", "mugshot", "inmate", "offender", "warrant", "felony", "conviction"}
    for acct in accounts:
        profile_text = json.dumps(acct.get("profile_data", {})).lower()
        found_legal = legal_keywords & set(profile_text.split())
        if found_legal:
            score += 15
            signals.append({
                "signal_type": "criminal_record_mention",
                "severity": "high",
                "detail": f"Criminal/legal keywords found on {acct.get('platform', 'unknown')}: {', '.join(found_legal)}.",
                "source": acct.get("source", "osint_engine"),
            })
            break  # One signal is sufficient

    # Social inactivity — no accounts found at all is also a risk signal
    if total == 0:
        score += 12
        signals.append({
            "signal_type": "social_inactivity",
            "severity": "medium",
            "detail": "No social media presence found — subject may be using aliases.",
            "source": "osint_engine",
        })

    return min(score, 40), signals  # Cap OSINT delta at 40 points


# ── Core Service Class ─────────────────────────────────────────────────────────

class OSINTService:
    """Orchestrates OSINT scans and manages report storage."""

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

    # ── Maigret ───────────────────────────────────────────────────────────────

    async def _run_maigret(
        self, username: str, deep: bool = False, tmpdir: str = ""
    ) -> Dict[str, Any]:
        """
        Run Maigret CLI and return parsed JSON results.
        Raises RuntimeError on tool failure.
        """
        if not username or len(username) < 2:
            return {}

        report_path = os.path.join(tmpdir, f"maigret_{username}.json")
        cmd = [
            MAIGRET_CMD,
            username,
            "--json", "simple",
            "--output", report_path,
            "--no-recursion",  # Avoid runaway recursive searches
            "--timeout", "15",
        ]
        if not deep:
            # Top-500 sites only for speed
            cmd += ["--top-sites", "500"]
        else:
            cmd += ["-a"]  # All sites

        log.info("Maigret scan initiated for subject %s", _redact(username))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=MAIGRET_TIMEOUT)
            if proc.returncode not in (0, 1):  # Maigret exits 1 if some sites fail
                log.warning("Maigret returned code %d: %s", proc.returncode, stderr[:200])

            if os.path.exists(report_path):
                with open(report_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except asyncio.TimeoutError:
            log.warning("Maigret timed out for subject %s", _redact(username))
        except FileNotFoundError:
            log.error("Maigret not found at '%s'. Install with: pip install maigret", MAIGRET_CMD)
        except Exception as exc:
            log.error("Maigret error for %s: %s", _redact(username), exc)

        return {}

    def _parse_maigret_json(self, raw: Dict[str, Any]) -> List[Dict]:
        """
        Parse Maigret's simple JSON format into a normalized account list.
        Maigret simple JSON: { "sites": { "SiteName": { "status": {...}, "url_user": "...", ... } } }
        """
        accounts = []
        sites = raw.get("sites", {})
        for site_name, site_data in sites.items():
            status = site_data.get("status", {})
            status_id = status.get("id", "")
            if status_id not in ("found", "claimed"):
                continue
            accounts.append({
                "platform": site_name,
                "url": site_data.get("url_user", ""),
                "username": site_data.get("username", ""),
                "profile_data": site_data.get("ids", {}),
                "source": "maigret",
                "confidence": "found",
            })
        return accounts

    # ── Blackbird ─────────────────────────────────────────────────────────────

    async def _run_blackbird(
        self, username: Optional[str] = None, email: Optional[str] = None, tmpdir: str = ""
    ) -> Dict[str, Any]:
        """
        Run Blackbird CLI and return parsed JSON results.
        Supports username or email search.
        """
        if not username and not email:
            return {}

        report_path = os.path.join(tmpdir, "blackbird_report.json")
        cmd = [PYTHON_CMD, BLACKBIRD_CMD]
        if username:
            cmd += ["--username", username]
        elif email:
            cmd += ["--email", email]
        cmd += ["--json", report_path]

        log.info(
            "Blackbird scan initiated for subject %s",
            _redact(username or email),
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=BLACKBIRD_DIR,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=BLACKBIRD_TIMEOUT)
            if proc.returncode not in (0, 1):
                log.warning("Blackbird returned code %d: %s", proc.returncode, stderr[:200])

            if os.path.exists(report_path):
                with open(report_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except asyncio.TimeoutError:
            log.warning("Blackbird timed out for subject %s", _redact(username or email))
        except FileNotFoundError:
            log.error(
                "Blackbird not found at '%s'. Clone from: https://github.com/p1ngul1n0/blackbird",
                BLACKBIRD_CMD,
            )
        except Exception as exc:
            log.error("Blackbird error for %s: %s", _redact(username or email), exc)

        return {}

    def _parse_blackbird_json(self, raw: Dict[str, Any]) -> List[Dict]:
        """
        Parse Blackbird's JSON output into a normalized account list.
        Blackbird JSON: { "results": [ { "name": "...", "url": "...", "status": "FOUND" } ] }
        """
        accounts = []
        results = raw.get("results", [])
        for item in results:
            if item.get("status", "").upper() != "FOUND":
                continue
            accounts.append({
                "platform": item.get("name", "Unknown"),
                "url": item.get("url", ""),
                "username": item.get("username", ""),
                "profile_data": item.get("metadata", {}),
                "source": "blackbird",
                "confidence": "found",
            })
        return accounts

    # ── AI Summary ────────────────────────────────────────────────────────────

    async def _generate_ai_summary(
        self, accounts: List[Dict], risk_signals: List[Dict], subject_type: str
    ) -> Optional[str]:
        """
        Generate a concise AI investigation summary using Gemini.
        Only platform names are sent — no PII.
        """
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        if not gemini_key or not accounts:
            return None

        try:
            import google.generativeai as genai  # type: ignore

            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel("gemini-2.5-flash")

            platform_list = ", ".join({a["platform"] for a in accounts[:30]})
            signal_list = "; ".join(s["detail"] for s in risk_signals[:5])

            prompt = (
                f"You are a bail bond risk analyst. A {subject_type} has been found on the "
                f"following platforms: {platform_list}. "
                f"Risk signals identified: {signal_list or 'none'}. "
                "Write a 2-3 sentence professional risk assessment summary for the bail bondsman. "
                "Do not include any personally identifiable information. "
                "Focus on flight risk indicators and financial reliability signals."
            )

            response = await asyncio.to_thread(model.generate_content, prompt)
            return response.text.strip()
        except Exception as exc:
            log.warning("AI summary generation failed: %s", exc)
            return None

    # ── Main Scan Orchestrator ─────────────────────────────────────────────────

    async def run_scan(
        self,
        subject_type: str,
        subject_id: str,
        full_name: Optional[str],
        usernames: Optional[List[str]],
        email: Optional[str],
        deep_scan: bool = False,
        run_maigret: bool = True,
        run_blackbird: bool = True,
        notes: Optional[str] = None,
        actor: str = "admin",
    ) -> str:
        """
        Orchestrate a full OSINT scan. Returns the MongoDB report _id as a string.
        The scan runs asynchronously — callers should poll GET /api/osint/report/{id}.
        """
        col = self._collection
        now = datetime.now(timezone.utc)

        # Insert a pending report document
        doc = {
            "subject_type": subject_type,
            "subject_id": subject_id,
            "scan_requested_by": actor,
            "scan_started_at": now,
            "scan_completed_at": None,
            "status": "running",
            "maigret_accounts": [],
            "blackbird_accounts": [],
            "total_accounts_found": 0,
            "platforms_found": [],
            "risk_signals": [],
            "osint_risk_score": 0,
            "ai_summary": None,
            "raw_maigret_json": None,
            "raw_blackbird_json": None,
            "error": None,
            "notes": notes,
            "created_at": now,
        }
        result = await col.insert_one(doc)
        report_id = str(result.inserted_id)

        # Audit the scan initiation (no PII in log)
        await AuditService.log_event(
            entity_type=subject_type,
            entity_id=subject_id,
            action="osint_scan_initiated",
            details={
                "report_id": report_id,
                "tools": {
                    "maigret": run_maigret,
                    "blackbird": run_blackbird,
                },
                "deep_scan": deep_scan,
            },
            actor=actor,
            actor_type="admin",
            event_context="osint_intelligence",
        )

        # Run scan in background task
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
        run_maigret: bool,
        run_blackbird: bool,
        actor: str,
    ) -> None:
        """Background task: runs tools, parses results, updates MongoDB."""
        col = self._collection

        # Build username candidates from name + explicit usernames
        username_candidates = list(usernames)
        if full_name:
            parts = full_name.lower().split()
            if len(parts) >= 2:
                first, last = parts[0], parts[-1]
                username_candidates += [
                    f"{first}{last}",
                    f"{first}.{last}",
                    f"{first}_{last}",
                    f"{first[0]}{last}",
                ]
        # Deduplicate and sanitize
        username_candidates = list({u.strip() for u in username_candidates if u and len(u) >= 3})

        all_maigret_accounts: List[Dict] = []
        all_blackbird_accounts: List[Dict] = []
        raw_maigret: Dict = {}
        raw_blackbird: Dict = {}
        error_msg: Optional[str] = None

        try:
            with tempfile.TemporaryDirectory(prefix="sl_osint_") as tmpdir:
                tasks = []

                # Maigret — run for each username candidate
                if run_maigret and username_candidates:
                    primary_username = username_candidates[0]
                    tasks.append(
                        self._run_maigret(primary_username, deep=deep_scan, tmpdir=tmpdir)
                    )
                else:
                    tasks.append(asyncio.sleep(0))  # placeholder

                # Blackbird — prefer email, fall back to username
                if run_blackbird:
                    bb_username = username_candidates[0] if username_candidates else None
                    tasks.append(
                        self._run_blackbird(username=bb_username, email=email, tmpdir=tmpdir)
                    )
                else:
                    tasks.append(asyncio.sleep(0))  # placeholder

                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Parse Maigret
                if run_maigret and not isinstance(results[0], Exception) and results[0]:
                    raw_maigret = results[0]
                    all_maigret_accounts = self._parse_maigret_json(raw_maigret)

                # Parse Blackbird
                if run_blackbird and not isinstance(results[1], Exception) and results[1]:
                    raw_blackbird = results[1]
                    all_blackbird_accounts = self._parse_blackbird_json(raw_blackbird)

                # If we have multiple username candidates, run Maigret on secondary ones
                if run_maigret and len(username_candidates) > 1:
                    for alt_username in username_candidates[1:3]:  # Max 3 total
                        alt_raw = await self._run_maigret(alt_username, deep=False, tmpdir=tmpdir)
                        if alt_raw:
                            alt_accounts = self._parse_maigret_json(alt_raw)
                            # Merge, deduplicating by platform+url
                            existing_keys = {(a["platform"], a["url"]) for a in all_maigret_accounts}
                            for acct in alt_accounts:
                                key = (acct["platform"], acct["url"])
                                if key not in existing_keys:
                                    all_maigret_accounts.append(acct)
                                    existing_keys.add(key)

        except Exception as exc:
            log.error("OSINT scan %s failed: %s", report_id, exc)
            error_msg = str(exc)

        # Combine all accounts
        all_accounts = all_maigret_accounts + all_blackbird_accounts
        platforms = list({a["platform"] for a in all_accounts})

        # Score and derive risk signals
        score_delta, signals = _score_signals(all_accounts, subject_type)

        # AI summary
        ai_summary = await self._generate_ai_summary(all_accounts, signals, subject_type)

        # Update the report document
        now = datetime.now(timezone.utc)
        update = {
            "$set": {
                "status": "failed" if error_msg else "complete",
                "scan_completed_at": now,
                "maigret_accounts": all_maigret_accounts,
                "blackbird_accounts": all_blackbird_accounts,
                "total_accounts_found": len(all_accounts),
                "platforms_found": platforms,
                "risk_signals": signals,
                "osint_risk_score": score_delta,
                "ai_summary": ai_summary,
                # Store raw JSON for forensic purposes — not exposed in default UI
                "raw_maigret_json": raw_maigret if raw_maigret else None,
                "raw_blackbird_json": raw_blackbird if raw_blackbird else None,
                "error": error_msg,
            }
        }
        await col.update_one({"_id": ObjectId(report_id)}, update)

        # Audit completion
        await AuditService.log_event(
            entity_type=subject_type,
            entity_id=subject_id,
            action="osint_scan_complete",
            details={
                "report_id": report_id,
                "accounts_found": len(all_accounts),
                "risk_score_delta": score_delta,
                "signals_count": len(signals),
                "status": "failed" if error_msg else "complete",
            },
            actor=actor,
            actor_type="admin",
            event_context="osint_intelligence",
        )

        log.info(
            "OSINT scan %s complete — %d accounts, score delta +%d",
            report_id,
            len(all_accounts),
            score_delta,
        )

    # ── Report Retrieval ──────────────────────────────────────────────────────

    async def get_report(self, report_id: str) -> Optional[Dict]:
        """Retrieve a report by its MongoDB _id."""
        try:
            doc = await self._collection.find_one({"_id": ObjectId(report_id)})
        except Exception:
            return None
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        # Strip raw JSON from default response — available via separate endpoint
        doc.pop("raw_maigret_json", None)
        doc.pop("raw_blackbird_json", None)
        return doc

    async def get_raw_report(self, report_id: str) -> Optional[Dict]:
        """Retrieve the full report including raw tool JSON (admin forensics)."""
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
        """List OSINT reports, optionally filtered by subject."""
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
        """
        Generate a Trape-style tracking session payload.

        This does NOT automatically start a Trape server. It generates the
        session record and the command the operator needs to run manually.
        Trape requires a publicly accessible server (ngrok or static IP) and
        the target to visit the generated URL.

        Returns the session document with operational instructions.
        """
        session_id = secrets.token_urlsafe(16)
        now = datetime.now(timezone.utc)

        # Trape server URL from environment (operator must configure this)
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
            # Operational instructions for the admin
            "trape_command": (
                f"python3 {os.path.join(os.getenv('TRAPE_DIR', '/opt/trape'), 'trape.py')} "
                f"--url {lure_url} --port 8099 --accesskey {session_id}"
            ),
        }

        result = await self._trape_col.insert_one(doc)
        doc["_id"] = str(result.inserted_id)

        # Audit
        await AuditService.log_event(
            entity_type=subject_type,
            entity_id=subject_id,
            action="trape_session_created",
            details={
                "session_id": session_id,
                "lure_url": lure_url,
            },
            actor=actor,
            actor_type="admin",
            event_context="osint_intelligence",
        )

        return doc

    async def update_trape_session(
        self, session_id: str, data: Dict, actor: str = "admin"
    ) -> bool:
        """
        Update a Trape session with collected data (called by Trape webhook or manual entry).
        """
        result = await self._trape_col.update_one(
            {"session_id": session_id},
            {
                "$set": {
                    **data,
                    "status": "triggered",
                    "updated_at": datetime.now(timezone.utc),
                }
            },
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
        """List Trape sessions."""
        query: Dict = {}
        if subject_id:
            query["subject_id"] = subject_id
        docs = []
        cursor = self._trape_col.find(query).sort("created_at", -1).limit(limit)
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            docs.append(doc)
        return docs


# ── Singleton ─────────────────────────────────────────────────────────────────

_service: Optional[OSINTService] = None


def get_osint_service() -> OSINTService:
    global _service
    if _service is None:
        _service = OSINTService()
    return _service
