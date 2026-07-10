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
import re
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
def _resolve_maigret_cmd() -> Optional[str]:
    """Locate maigret executable (PATH, env, common venv paths)."""
    candidates = [
        os.getenv("MAIGRET_PATH", "").strip(),
        shutil.which("maigret") or "",
        "/usr/local/bin/maigret",
        "/usr/bin/maigret",
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
        # env may point to a non-executable path string that still works via python -m
        if c and os.path.isfile(c):
            return c
    # python -m maigret fallback
    try:
        import importlib.util
        if importlib.util.find_spec("maigret") is not None:
            return "python-module"
    except Exception:
        pass
    return None


def _resolve_blackbird() -> tuple[Optional[str], Optional[str]]:
    """Return (blackbird_dir, blackbird.py path) or (None, None)."""
    dirs = [
        os.getenv("BLACKBIRD_DIR", "").strip(),
        "/opt/blackbird",
        os.path.expanduser("~/blackbird"),
    ]
    for d in dirs:
        if not d:
            continue
        script = os.path.join(d, "blackbird.py")
        if os.path.isfile(script):
            return d, script
    return None, None


MAIGRET_CMD = _resolve_maigret_cmd() or os.getenv("MAIGRET_PATH", "maigret")
BLACKBIRD_DIR, BLACKBIRD_CMD = _resolve_blackbird()
if not BLACKBIRD_DIR:
    BLACKBIRD_DIR = os.getenv("BLACKBIRD_DIR", "/opt/blackbird")
    BLACKBIRD_CMD = os.path.join(BLACKBIRD_DIR, "blackbird.py")
PYTHON_CMD = shutil.which("python3") or shutil.which("python") or "python3"

# Timeout for each tool (seconds)
MAIGRET_TIMEOUT = int(os.getenv("OSINT_MAIGRET_TIMEOUT", "180"))
BLACKBIRD_TIMEOUT = int(os.getenv("OSINT_BLACKBIRD_TIMEOUT", "120"))

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

    # ── Tool probe ────────────────────────────────────────────────────────────

    @staticmethod
    def probe_tools() -> Dict[str, Any]:
        """
        Synchronous tool availability + version probe (safe for status API).
        """
        maigret_cmd = _resolve_maigret_cmd()
        bb_dir, bb_script = _resolve_blackbird()
        trape_dir = os.getenv("TRAPE_DIR", "/opt/trape")
        trape_path = os.path.join(trape_dir, "trape.py")
        trape_server = os.getenv("TRAPE_SERVER_URL", "")

        maigret_ok = False
        maigret_version = None
        maigret_error = None
        if maigret_cmd == "python-module":
            try:
                import maigret as _m  # type: ignore
                maigret_ok = True
                maigret_version = getattr(_m, "__version__", "installed")
                maigret_cmd = f"{PYTHON_CMD} -m maigret"
            except Exception as e:
                maigret_error = str(e)[:200]
                maigret_cmd = None
        elif maigret_cmd and (os.path.isfile(maigret_cmd) or shutil.which(str(maigret_cmd).split()[0])):
            maigret_ok = True
            try:
                import subprocess
                r = subprocess.run(
                    [maigret_cmd, "--version"],
                    capture_output=True, text=True, timeout=8,
                )
                maigret_version = (r.stdout or r.stderr or "").strip().split("\n")[0][:80]
            except Exception as e:
                maigret_version = f"unknown ({e})"
        else:
            maigret_error = "not found — install with: pip install maigret"

        blackbird_ok = bool(bb_script and os.path.isfile(bb_script))
        blackbird_error = None if blackbird_ok else (
            "not found — clone https://github.com/p1ngul1n0/blackbird to /opt/blackbird"
        )

        return {
            "maigret": {
                "available": maigret_ok,
                "path": maigret_cmd or "not found",
                "version": maigret_version,
                "error": maigret_error,
            },
            "blackbird": {
                "available": blackbird_ok,
                "path": bb_script or "not found",
                "dir": bb_dir,
                "error": blackbird_error,
            },
            "trape": {
                "available": os.path.isfile(trape_path),
                "path": trape_path if os.path.isfile(trape_path) else "not found",
                "server_url": trape_server or "not configured — set TRAPE_SERVER_URL",
                "note": (
                    "Trape requires a separate public server. "
                    "Use /api/osint/trape/session for operator payloads only."
                ),
            },
            "ready_for_scans": maigret_ok or blackbird_ok,
        }

    # ── Maigret ───────────────────────────────────────────────────────────────

    async def _run_maigret(
        self, username: str, deep: bool = False, tmpdir: str = ""
    ) -> Dict[str, Any]:
        """
        Run Maigret CLI and return parsed JSON results + meta.
        Maigret v0.6+ writes: {folder}/report_{username}_simple.json
        Flags: -J simple -fo PATH (NOT --output file)
        """
        result_meta: Dict[str, Any] = {
            "tool": "maigret",
            "ok": False,
            "error": None,
            "raw": {},
            "accounts": [],
        }
        if not username or len(username) < 2:
            result_meta["error"] = "username too short"
            return result_meta

        maigret_cmd = _resolve_maigret_cmd()
        if not maigret_cmd:
            result_meta["error"] = "maigret not installed"
            return result_meta

        safe_user = re.sub(r"[^\w.\-]", "_", username)[:64]
        out_dir = tmpdir or tempfile.mkdtemp(prefix="maigret_")

        if maigret_cmd == "python-module":
            cmd = [PYTHON_CMD, "-m", "maigret"]
        else:
            cmd = [maigret_cmd]

        # Maigret 0.6+ requires a writable HOME (~/.maigret) and may rewrite
        # its sites DB on exit. Use a private temp home + writable --db copy so
        # container RO site-packages / missing /home/appuser do not kill scans.
        maigret_home = os.path.join(out_dir, ".maigret_home")
        os.makedirs(os.path.join(maigret_home, ".cache"), exist_ok=True)
        db_path = os.path.join(maigret_home, "data.json")
        try:
            import maigret as _maigret_mod  # type: ignore
            pkg_dir = os.path.dirname(getattr(_maigret_mod, "__file__", "") or "")
            src_db = os.path.join(pkg_dir, "resources", "data.json")
            if os.path.isfile(src_db) and not os.path.isfile(db_path):
                shutil.copy2(src_db, db_path)
        except Exception:
            pass

        cmd += [
            username,
            "-J", "simple",
            "-fo", out_dir,
            "--no-recursion",
            "--no-autoupdate",
            "--timeout", "15",
            "--no-progressbar",
            "--no-color",
        ]
        if os.path.isfile(db_path):
            cmd += ["--db", db_path]
        if not deep:
            cmd += ["--top-sites", "500"]
        else:
            cmd += ["-a"]

        log.info("Maigret scan initiated for subject %s", _redact(username))

        child_env = os.environ.copy()
        child_env["HOME"] = maigret_home
        child_env["XDG_CACHE_HOME"] = os.path.join(maigret_home, ".cache")
        child_env["XDG_CONFIG_HOME"] = os.path.join(maigret_home, ".config")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_env,
                cwd=out_dir,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=MAIGRET_TIMEOUT
            )
            stderr_s = (stderr or b"").decode("utf-8", errors="replace")
            stdout_s = (stdout or b"").decode("utf-8", errors="replace")

            # Prefer report_USERNAME_simple.json in folderoutput
            candidates = [
                os.path.join(out_dir, f"report_{username}_simple.json"),
                os.path.join(out_dir, f"report_{safe_user}_simple.json"),
            ]
            # fallback: any *simple*.json in out_dir
            if os.path.isdir(out_dir):
                for fn in os.listdir(out_dir):
                    if fn.endswith("_simple.json") or (
                        fn.endswith(".json") and "simple" in fn
                    ):
                        candidates.append(os.path.join(out_dir, fn))

            raw = {}
            report_path = next((p for p in candidates if os.path.isfile(p)), None)
            if report_path:
                with open(report_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            elif proc.returncode not in (0, 1):
                result_meta["error"] = (
                    f"maigret exit {proc.returncode}: "
                    f"{(stderr_s or stdout_s)[:300] or 'no output'}"
                )
                return result_meta
            else:
                result_meta["error"] = (
                    "maigret finished but no JSON report found in folderoutput"
                )
                result_meta["stderr_tail"] = stderr_s[-400:]
                return result_meta

            accounts = self._parse_maigret_json(raw)
            result_meta.update({
                "ok": True,
                "raw": raw,
                "accounts": accounts,
                "report_path": report_path,
            })
            if not accounts:
                result_meta["warning"] = "maigret ran successfully but found 0 accounts"
            return result_meta

        except asyncio.TimeoutError:
            result_meta["error"] = f"maigret timed out after {MAIGRET_TIMEOUT}s"
            log.warning("Maigret timed out for subject %s", _redact(username))
        except FileNotFoundError:
            result_meta["error"] = f"maigret executable not found ({maigret_cmd})"
            log.error("Maigret not found at '%s'", maigret_cmd)
        except Exception as exc:
            result_meta["error"] = f"maigret error: {exc}"
            log.error("Maigret error for %s: %s", _redact(username), exc)

        return result_meta

    def _parse_maigret_json(self, raw: Any) -> List[Dict]:
        """
        Parse Maigret JSON into a normalized account list.

        Supports:
          - simple report: { "SiteName": { "status": {"status": "Claimed", "url": "..."}, "url_user": "..." } }
          - nested: { "sites": { ... } }
        """
        accounts: List[Dict] = []
        if not raw:
            return accounts

        if isinstance(raw, dict) and "sites" in raw and isinstance(raw["sites"], dict):
            sites = raw["sites"]
        elif isinstance(raw, dict):
            sites = raw
        else:
            return accounts

        for site_name, site_data in sites.items():
            if not isinstance(site_data, dict):
                continue
            # Skip meta keys if present
            if site_name in ("username", "type", "generated_at"):
                continue

            status_blob = site_data.get("status", {})
            if isinstance(status_blob, dict):
                st = (
                    status_blob.get("status")
                    or status_blob.get("id")
                    or ""
                )
                st = str(st).lower().strip()
                url = (
                    status_blob.get("url")
                    or site_data.get("url_user")
                    or ""
                )
                uname = (
                    status_blob.get("username")
                    or site_data.get("username")
                    or ""
                )
                ids = status_blob.get("ids") or site_data.get("ids") or {}
            else:
                st = str(status_blob or "").lower().strip()
                url = site_data.get("url_user") or site_data.get("url") or ""
                uname = site_data.get("username") or ""
                ids = site_data.get("ids") or {}

            # Claimed / Found = real hit (Maigret uses "Claimed")
            if st not in ("found", "claimed"):
                continue

            accounts.append({
                "platform": str(site_name),
                "url": url,
                "username": uname,
                "profile_data": ids if isinstance(ids, dict) else {},
                "source": "maigret",
                "confidence": "found",
            })
        return accounts

    # ── Blackbird ─────────────────────────────────────────────────────────────

    async def _run_blackbird(
        self, username: Optional[str] = None, email: Optional[str] = None, tmpdir: str = ""
    ) -> Dict[str, Any]:
        """
        Run Blackbird CLI and return parsed results + meta.

        Blackbird v2: `--json` is a boolean flag (writes under blackbird/results/),
        NOT `--json /path/to/file`.
        """
        result_meta: Dict[str, Any] = {
            "tool": "blackbird",
            "ok": False,
            "error": None,
            "raw": {},
            "accounts": [],
        }
        if not username and not email:
            result_meta["error"] = "username or email required"
            return result_meta

        bb_dir, bb_script = _resolve_blackbird()
        if not bb_script or not bb_dir:
            result_meta["error"] = "blackbird not installed at BLACKBIRD_DIR"
            return result_meta

        cmd = [PYTHON_CMD, bb_script, "--json", "--no-update"]
        if username:
            cmd += ["--username", username]
        if email:
            cmd += ["--email", email]

        log.info(
            "Blackbird scan initiated for subject %s",
            _redact(username or email),
        )

        results_root = os.path.join(bb_dir, "results")
        before = set()
        if os.path.isdir(results_root):
            for root, _, files in os.walk(results_root):
                for f in files:
                    if f.endswith(".json"):
                        before.add(os.path.join(root, f))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=bb_dir,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=BLACKBIRD_TIMEOUT
            )
            stderr_s = (stderr or b"").decode("utf-8", errors="replace")
            stdout_s = (stdout or b"").decode("utf-8", errors="replace")

            # Newest JSON under results/ not present before the run
            after: List[str] = []
            if os.path.isdir(results_root):
                for root, _, files in os.walk(results_root):
                    for f in files:
                        if f.endswith(".json"):
                            p = os.path.join(root, f)
                            if p not in before:
                                after.append(p)
            # fallback: any recent json
            if not after and os.path.isdir(results_root):
                candidates = []
                for root, _, files in os.walk(results_root):
                    for f in files:
                        if f.endswith(".json"):
                            p = os.path.join(root, f)
                            candidates.append((os.path.getmtime(p), p))
                candidates.sort(reverse=True)
                after = [p for _, p in candidates[:3]]

            raw: Any = {}
            if after:
                after.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                with open(after[0], "r", encoding="utf-8") as f:
                    raw = json.load(f)
            elif proc.returncode not in (0, 1):
                result_meta["error"] = (
                    f"blackbird exit {proc.returncode}: "
                    f"{(stderr_s or stdout_s)[:300] or 'no output'}"
                )
                return result_meta
            else:
                # Try parse stdout as JSON array
                try:
                    raw = json.loads(stdout_s) if stdout_s.strip().startswith(("[", "{")) else {}
                except Exception:
                    raw = {}
                if not raw:
                    result_meta["error"] = (
                        "blackbird finished but no JSON results file found under results/"
                    )
                    result_meta["stderr_tail"] = stderr_s[-400:]
                    return result_meta

            accounts = self._parse_blackbird_json(raw)
            result_meta.update({
                "ok": True,
                "raw": raw if isinstance(raw, dict) else {"results": raw},
                "accounts": accounts,
            })
            if not accounts:
                result_meta["warning"] = "blackbird ran successfully but found 0 accounts"
            return result_meta

        except asyncio.TimeoutError:
            result_meta["error"] = f"blackbird timed out after {BLACKBIRD_TIMEOUT}s"
            log.warning("Blackbird timed out for subject %s", _redact(username or email))
        except FileNotFoundError:
            result_meta["error"] = f"blackbird not found at {bb_script}"
            log.error("Blackbird not found at '%s'", bb_script)
        except Exception as exc:
            result_meta["error"] = f"blackbird error: {exc}"
            log.error("Blackbird error for %s: %s", _redact(username or email), exc)

        return result_meta

    def _parse_blackbird_json(self, raw: Any) -> List[Dict]:
        """
        Parse Blackbird JSON into normalized accounts.

        Formats:
          - list of account dicts (current export)
          - { "results": [ ... ] }
        Account dicts use name/url/status (FOUND).
        """
        accounts: List[Dict] = []
        if isinstance(raw, list):
            results = raw
        elif isinstance(raw, dict):
            results = raw.get("results") or raw.get("sites") or raw.get("accounts") or []
            if not results and raw.get("name") and raw.get("url"):
                results = [raw]
        else:
            return accounts

        for item in results:
            if not isinstance(item, dict):
                continue
            st = str(item.get("status") or item.get("Status") or "FOUND").upper()
            # Blackbird may omit status on export of found-only lists
            if st and st not in ("FOUND", "CLAIMED", "TRUE", "1", "OK"):
                # skip explicit not-found
                if st in ("NOT FOUND", "NOT_FOUND", "FALSE", "0"):
                    continue
            platform = item.get("name") or item.get("site") or item.get("platform") or "Unknown"
            url = item.get("url") or item.get("app") or ""
            if not url and not platform:
                continue
            accounts.append({
                "platform": str(platform),
                "url": str(url),
                "username": item.get("username") or item.get("user") or "",
                "profile_data": item.get("metadata") or item.get("data") or {},
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
            "full_name": full_name,
            "scan_requested_by": actor,
            "scan_started_at": now,
            "scan_completed_at": None,
            "status": "running",
            "run_maigret": run_maigret,
            "run_blackbird": run_blackbird,
            "maigret_accounts": [],
            "blackbird_accounts": [],
            "total_accounts_found": 0,
            "platforms_found": [],
            "risk_signals": [],
            "osint_risk_score": 0,
            "ai_summary": None,
            "raw_maigret_json": None,
            "raw_blackbird_json": None,
            "tool_results": {},
            "warnings": [],
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
        """
        Background task: runs tools, parses results, updates MongoDB.

        Fail-loud rules (never look "complete with 0 accounts" on tool failure):
          - Requested tool missing / crashed / no report file → failed or partial
          - All requested tools failed → status=failed
          - Mix of ok + failed → status=partial when any accounts found, else failed
          - Tools ok with 0 hits → status=complete (legitimate empty) + warnings
        """
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
        # Deduplicate and sanitize (preserve order: explicit usernames first)
        seen: set[str] = set()
        ordered: List[str] = []
        for u in username_candidates:
            key = (u or "").strip()
            if len(key) < 3 or key.lower() in seen:
                continue
            seen.add(key.lower())
            ordered.append(key)
        username_candidates = ordered

        all_maigret_accounts: List[Dict] = []
        all_blackbird_accounts: List[Dict] = []
        raw_maigret: Any = None
        raw_blackbird: Any = None
        tool_results: Dict[str, Any] = {}
        warnings: List[str] = []
        error_parts: List[str] = []
        fatal_exc: Optional[str] = None

        # Pre-flight: tools actually present when requested
        probe = self.probe_tools()
        maigret_available = bool(probe.get("maigret", {}).get("available"))
        blackbird_available = bool(probe.get("blackbird", {}).get("available"))

        want_maigret = bool(run_maigret)
        want_blackbird = bool(run_blackbird)

        if want_maigret and not maigret_available:
            error_parts.append(
                "maigret not installed in this container — "
                + str(probe.get("maigret", {}).get("error") or "pip install maigret")
            )
            tool_results["maigret"] = {
                "ok": False,
                "error": "not installed",
                "attempted": False,
            }
            want_maigret = False  # do not attempt subprocess

        if want_blackbird and not blackbird_available:
            error_parts.append(
                "blackbird not installed — "
                + str(probe.get("blackbird", {}).get("error") or "clone to /opt/blackbird")
            )
            tool_results["blackbird"] = {
                "ok": False,
                "error": "not installed",
                "attempted": False,
            }
            want_blackbird = False

        if want_maigret and not username_candidates:
            error_parts.append(
                "maigret requested but no username candidates "
                "(provide usernames or a full first+last name)"
            )
            tool_results["maigret"] = {
                "ok": False,
                "error": "no username candidates",
                "attempted": False,
            }
            want_maigret = False

        if want_blackbird and not username_candidates and not email:
            error_parts.append(
                "blackbird requested but no username or email provided"
            )
            tool_results["blackbird"] = {
                "ok": False,
                "error": "no username or email",
                "attempted": False,
            }
            want_blackbird = False

        try:
            with tempfile.TemporaryDirectory(prefix="sl_osint_") as tmpdir:
                # ── Maigret primary ──────────────────────────────────────────
                if want_maigret:
                    primary_username = username_candidates[0]
                    mg = await self._run_maigret(
                        primary_username, deep=deep_scan, tmpdir=tmpdir
                    )
                    if isinstance(mg, Exception):
                        tool_results["maigret"] = {
                            "ok": False,
                            "error": str(mg),
                            "attempted": True,
                        }
                        error_parts.append(f"maigret: {mg}")
                    else:
                        raw_maigret = mg.get("raw") or {}
                        accounts = list(mg.get("accounts") or [])
                        all_maigret_accounts.extend(accounts)
                        entry = {
                            "ok": bool(mg.get("ok")),
                            "error": mg.get("error"),
                            "warning": mg.get("warning"),
                            "attempted": True,
                            "accounts": len(accounts),
                            "username": primary_username[:64],
                        }
                        tool_results["maigret"] = entry
                        if not mg.get("ok"):
                            error_parts.append(
                                f"maigret: {mg.get('error') or 'failed'}"
                            )
                        elif mg.get("warning"):
                            warnings.append(str(mg["warning"]))

                    # Secondary username candidates (max 3 total)
                    if len(username_candidates) > 1 and tool_results.get("maigret", {}).get("ok"):
                        existing_keys = {
                            (a["platform"], a["url"]) for a in all_maigret_accounts
                        }
                        alt_meta = []
                        for alt_username in username_candidates[1:3]:
                            alt = await self._run_maigret(
                                alt_username, deep=False, tmpdir=tmpdir
                            )
                            if not isinstance(alt, dict) or not alt.get("ok"):
                                alt_meta.append({
                                    "username": alt_username[:64],
                                    "ok": False,
                                    "error": (alt or {}).get("error") if isinstance(alt, dict) else str(alt),
                                })
                                continue
                            for acct in alt.get("accounts") or []:
                                key = (acct["platform"], acct["url"])
                                if key not in existing_keys:
                                    all_maigret_accounts.append(acct)
                                    existing_keys.add(key)
                            alt_meta.append({
                                "username": alt_username[:64],
                                "ok": True,
                                "accounts": len(alt.get("accounts") or []),
                            })
                        tool_results["maigret"]["alt_usernames"] = alt_meta
                        tool_results["maigret"]["accounts"] = len(all_maigret_accounts)

                # ── Blackbird ────────────────────────────────────────────────
                if want_blackbird:
                    bb_username = username_candidates[0] if username_candidates else None
                    bb = await self._run_blackbird(
                        username=bb_username, email=email, tmpdir=tmpdir
                    )
                    if isinstance(bb, Exception):
                        tool_results["blackbird"] = {
                            "ok": False,
                            "error": str(bb),
                            "attempted": True,
                        }
                        error_parts.append(f"blackbird: {bb}")
                    else:
                        raw_blackbird = bb.get("raw") or {}
                        accounts = list(bb.get("accounts") or [])
                        all_blackbird_accounts.extend(accounts)
                        tool_results["blackbird"] = {
                            "ok": bool(bb.get("ok")),
                            "error": bb.get("error"),
                            "warning": bb.get("warning"),
                            "attempted": True,
                            "accounts": len(accounts),
                        }
                        if not bb.get("ok"):
                            error_parts.append(
                                f"blackbird: {bb.get('error') or 'failed'}"
                            )
                        elif bb.get("warning"):
                            warnings.append(str(bb["warning"]))

        except Exception as exc:
            log.error("OSINT scan %s failed: %s", report_id, exc)
            fatal_exc = str(exc)
            error_parts.append(fatal_exc)

        # Combine all accounts (dedupe platform+url across tools)
        deduped: List[Dict] = []
        seen_acct: set = set()
        for a in all_maigret_accounts + all_blackbird_accounts:
            key = (a.get("platform"), a.get("url"), a.get("source"))
            if key in seen_acct:
                continue
            seen_acct.add(key)
            deduped.append(a)
        all_accounts = deduped
        platforms = list({a["platform"] for a in all_accounts if a.get("platform")})

        # ── Status decision (fail loud) ────────────────────────────────────
        attempted = [
            k for k, v in tool_results.items()
            if isinstance(v, dict) and v.get("attempted")
        ]
        succeeded = [
            k for k, v in tool_results.items()
            if isinstance(v, dict) and v.get("ok")
        ]
        failed_tools = [
            k for k, v in tool_results.items()
            if isinstance(v, dict) and not v.get("ok")
        ]
        any_tool_requested = bool(run_maigret or run_blackbird)

        if fatal_exc:
            status = "failed"
        elif not any_tool_requested:
            status = "failed"
            error_parts.append("no tools selected for scan")
        elif not attempted and failed_tools:
            # All requested tools blocked pre-flight (missing install / no inputs)
            status = "failed"
        elif attempted and not succeeded:
            status = "failed"
        elif failed_tools and succeeded:
            status = "partial" if all_accounts else "failed"
        elif succeeded and not all_accounts:
            # Legitimate empty result — tools ran correctly
            status = "complete"
            warnings.append(
                "Scan completed successfully but found 0 accounts. "
                "Try alternate usernames, email, or deep scan."
            )
        else:
            status = "complete"

        error_msg = "; ".join(error_parts) if error_parts else None
        # Never present tool-failure as a quiet empty complete
        if status == "failed" and not error_msg:
            error_msg = "OSINT scan failed — no tools produced results"
        if status == "partial" and not error_msg:
            error_msg = f"Partial results — failed tools: {', '.join(failed_tools)}"

        # Score and derive risk signals
        # Only apply "social_inactivity" when tools actually succeeded empty
        score_delta, signals = _score_signals(all_accounts, subject_type)
        if status == "failed":
            # Strip misleading "no social presence" signal when scan didn't run
            signals = [
                s for s in signals
                if s.get("signal_type") != "social_inactivity"
            ]
            score_delta = min(
                sum(
                    15 if s.get("severity") == "high" else 8
                    for s in signals
                ),
                40,
            ) if signals else 0
            signals.append({
                "signal_type": "osint_scan_failed",
                "severity": "high",
                "detail": error_msg or "OSINT tools failed or are not installed.",
                "source": "osint_engine",
            })
        elif status == "partial":
            signals.append({
                "signal_type": "osint_partial",
                "severity": "medium",
                "detail": error_msg or "One or more OSINT tools failed.",
                "source": "osint_engine",
            })

        # AI summary only when we have accounts
        ai_summary = await self._generate_ai_summary(all_accounts, signals, subject_type)

        now = datetime.now(timezone.utc)
        update = {
            "$set": {
                "status": status,
                "scan_completed_at": now,
                "maigret_accounts": all_maigret_accounts,
                "blackbird_accounts": all_blackbird_accounts,
                "total_accounts_found": len(all_accounts),
                "platforms_found": platforms,
                "risk_signals": signals,
                "osint_risk_score": score_delta,
                "ai_summary": ai_summary,
                "raw_maigret_json": raw_maigret if raw_maigret else None,
                "raw_blackbird_json": raw_blackbird if raw_blackbird else None,
                "tool_results": tool_results,
                "warnings": warnings,
                "error": error_msg,
                "run_maigret": run_maigret,
                "run_blackbird": run_blackbird,
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
                "accounts_found": len(all_accounts),
                "risk_score_delta": score_delta,
                "signals_count": len(signals),
                "status": status,
                "tools_ok": succeeded,
                "tools_failed": failed_tools,
            },
            actor=actor,
            actor_type="admin",
            event_context="osint_intelligence",
        )

        log.info(
            "OSINT scan %s %s — %d accounts, score delta +%d, tools_ok=%s failed=%s",
            report_id,
            status,
            len(all_accounts),
            score_delta,
            succeeded,
            failed_tools,
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
