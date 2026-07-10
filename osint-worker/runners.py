"""
CLI runners for Maigret + Blackbird — osint-worker
Writable filesystem assumed (not read-only dashboard rootfs).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from typing import Any, Dict, List, Optional

from defaults import (
    BLACKBIRD_TIMEOUT,
    MAIGRET_NO_AUTOUPDATE,
    MAIGRET_NO_RECURSION,
    MAIGRET_SITE_TIMEOUT,
    MAIGRET_TIMEOUT,
    MAX_MAIGRET_USERNAMES,
    assess_maigret_quality,
    maigret_site_args,
)

log = logging.getLogger("osint_worker.runners")
PYTHON_CMD = shutil.which("python3") or shutil.which("python") or "python3"


def _redact(value: Optional[str]) -> str:
    if not value:
        return "[empty]"
    return f"[redacted:{hashlib.sha256(value.encode()).hexdigest()[:8]}]"


def resolve_maigret_cmd() -> Optional[str]:
    candidates = [
        os.getenv("MAIGRET_PATH", "").strip(),
        shutil.which("maigret") or "",
        "/usr/local/bin/maigret",
        "/usr/bin/maigret",
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
        if c and os.path.isfile(c):
            return c
    try:
        import importlib.util
        if importlib.util.find_spec("maigret") is not None:
            return "python-module"
    except Exception:
        pass
    return None


def resolve_blackbird() -> tuple[Optional[str], Optional[str]]:
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


def probe_tools() -> Dict[str, Any]:
    maigret_cmd = resolve_maigret_cmd()
    bb_dir, bb_script = resolve_blackbird()

    maigret_ok = False
    maigret_version = None
    maigret_error = None
    maigret_path = None

    if maigret_cmd == "python-module":
        try:
            import maigret as _m  # type: ignore
            maigret_ok = True
            maigret_version = getattr(_m, "__version__", "installed")
            maigret_path = f"{PYTHON_CMD} -m maigret"
        except Exception as e:
            maigret_error = str(e)[:200]
    elif maigret_cmd:
        maigret_ok = True
        maigret_path = maigret_cmd
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
    wmn = os.path.join(bb_dir or "", "data", "wmn-data.json") if bb_dir else ""
    if blackbird_ok and (not os.path.isfile(wmn) or os.path.getsize(wmn) < 1000):
        blackbird_ok = False
        blackbird_error = "missing data/wmn-data.json (WhatsMyName list)"
    else:
        blackbird_error = None if blackbird_ok else (
            "not found — clone https://github.com/p1ngul1n0/blackbird to /opt/blackbird"
        )

    return {
        "maigret": {
            "available": maigret_ok,
            "path": maigret_path or "not found",
            "version": maigret_version,
            "error": maigret_error,
        },
        "blackbird": {
            "available": blackbird_ok,
            "path": bb_script or "not found",
            "dir": bb_dir,
            "error": blackbird_error,
        },
        "ready_for_scans": maigret_ok or blackbird_ok,
        "worker": True,
        "defaults": {
            "maigret_default": True,
            "blackbird_default": False,
            "blackbird_on_email": True,
            "blackbird_on_second_opinion": True,
            "no_recursion": MAIGRET_NO_RECURSION,
        },
    }


def parse_maigret_json(raw: Any) -> List[Dict]:
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
        if site_name in ("username", "type", "generated_at"):
            continue
        status_blob = site_data.get("status", {})
        if isinstance(status_blob, dict):
            st = str(
                status_blob.get("status") or status_blob.get("id") or ""
            ).lower().strip()
            url = status_blob.get("url") or site_data.get("url_user") or ""
            uname = status_blob.get("username") or site_data.get("username") or ""
            ids = status_blob.get("ids") or site_data.get("ids") or {}
        else:
            st = str(status_blob or "").lower().strip()
            url = site_data.get("url_user") or site_data.get("url") or ""
            uname = site_data.get("username") or ""
            ids = site_data.get("ids") or {}
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


def parse_blackbird_json(raw: Any) -> List[Dict]:
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
        if st and st not in ("FOUND", "CLAIMED", "TRUE", "1", "OK"):
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


async def run_maigret(
    username: str,
    deep: bool = False,
    tmpdir: str = "",
) -> Dict[str, Any]:
    result_meta: Dict[str, Any] = {
        "tool": "maigret",
        "ok": False,
        "error": None,
        "warning": None,
        "raw": {},
        "accounts": [],
        "quality": {},
    }
    if not username or len(username) < 2:
        result_meta["error"] = "username too short"
        return result_meta

    maigret_cmd = resolve_maigret_cmd()
    if not maigret_cmd:
        result_meta["error"] = "maigret not installed"
        return result_meta

    safe_user = re.sub(r"[^\w.\-]", "_", username)[:64]
    out_dir = tmpdir or tempfile.mkdtemp(prefix="maigret_")
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

    if maigret_cmd == "python-module":
        cmd = [PYTHON_CMD, "-m", "maigret"]
    else:
        cmd = [maigret_cmd]

    cmd += [
        username,
        "-J", "simple",
        "-fo", out_dir,
        "--timeout", str(MAIGRET_SITE_TIMEOUT),
        "--no-progressbar",
        "--no-color",
    ]
    if MAIGRET_NO_RECURSION:
        cmd += ["--no-recursion"]
    if MAIGRET_NO_AUTOUPDATE:
        cmd += ["--no-autoupdate"]
    if os.path.isfile(db_path):
        cmd += ["--db", db_path]
    cmd += maigret_site_args(deep)

    log.info("Maigret scan for %s deep=%s", _redact(username), deep)

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
        quality = assess_maigret_quality(stderr_s, stdout_s)
        result_meta["quality"] = quality

        candidates = [
            os.path.join(out_dir, f"report_{username}_simple.json"),
            os.path.join(out_dir, f"report_{safe_user}_simple.json"),
        ]
        if os.path.isdir(out_dir):
            for fn in os.listdir(out_dir):
                if fn.endswith("_simple.json") or (fn.endswith(".json") and "simple" in fn):
                    candidates.append(os.path.join(out_dir, fn))

        # Prefer report matching primary username (avoid recursion spill files if any)
        report_path = None
        for p in candidates:
            if os.path.isfile(p) and (
                f"report_{username}" in os.path.basename(p)
                or f"report_{safe_user}" in os.path.basename(p)
            ):
                report_path = p
                break
        if not report_path:
            report_path = next((p for p in candidates if os.path.isfile(p)), None)

        if not report_path:
            result_meta["error"] = (
                f"maigret finished but no JSON report found "
                f"(exit {proc.returncode}): {(stderr_s or stdout_s)[:300] or 'no output'}"
            )
            result_meta["stderr_tail"] = stderr_s[-400:]
            return result_meta

        with open(report_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        accounts = parse_maigret_json(raw)
        result_meta.update({
            "ok": True,
            "raw": raw,
            "accounts": accounts,
            "report_path": report_path,
        })
        if quality.get("degraded"):
            result_meta["warning"] = (
                "maigret quality degraded: " + "; ".join(quality.get("reasons") or [])
            )
        if not accounts:
            result_meta["warning"] = (
                (result_meta.get("warning") + "; " if result_meta.get("warning") else "")
                + "maigret ran successfully but found 0 accounts"
            )
        return result_meta

    except asyncio.TimeoutError:
        result_meta["error"] = f"maigret timed out after {MAIGRET_TIMEOUT}s"
    except FileNotFoundError:
        result_meta["error"] = f"maigret executable not found ({maigret_cmd})"
    except Exception as exc:
        result_meta["error"] = f"maigret error: {exc}"
        log.error("Maigret error for %s: %s", _redact(username), exc)
    return result_meta


async def run_blackbird(
    username: Optional[str] = None,
    email: Optional[str] = None,
) -> Dict[str, Any]:
    result_meta: Dict[str, Any] = {
        "tool": "blackbird",
        "ok": False,
        "error": None,
        "warning": None,
        "raw": {},
        "accounts": [],
    }
    if not username and not email:
        result_meta["error"] = "username or email required"
        return result_meta

    bb_dir, bb_script = resolve_blackbird()
    if not bb_script or not bb_dir:
        result_meta["error"] = "blackbird not installed at BLACKBIRD_DIR"
        return result_meta

    wmn_path = os.path.join(bb_dir, "data", "wmn-data.json")
    if not os.path.isfile(wmn_path) or os.path.getsize(wmn_path) < 1000:
        result_meta["error"] = "blackbird missing data/wmn-data.json"
        return result_meta

    # Ensure writable logs/results
    os.makedirs(os.path.join(bb_dir, "results"), exist_ok=True)
    os.makedirs(os.path.join(bb_dir, "logs"), exist_ok=True)

    cmd = [PYTHON_CMD, bb_script, "--json", "--no-update", "--no-nsfw"]
    if username:
        cmd += ["--username", username]
    if email:
        cmd += ["--email", email]

    log.info("Blackbird scan for %s", _redact(username or email))

    results_root = os.path.join(bb_dir, "results")
    before: set = set()
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

        after: List[str] = []
        if os.path.isdir(results_root):
            for root, _, files in os.walk(results_root):
                for f in files:
                    if f.endswith(".json"):
                        p = os.path.join(root, f)
                        if p not in before:
                            after.append(p)
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
        else:
            try:
                raw = json.loads(stdout_s) if stdout_s.strip().startswith(("[", "{")) else {}
            except Exception:
                raw = {}
            if not raw:
                result_meta["error"] = (
                    f"blackbird finished but no JSON results "
                    f"(exit {proc.returncode}): {(stderr_s or stdout_s)[:300] or 'no output'}"
                )
                result_meta["stderr_tail"] = stderr_s[-400:]
                return result_meta

        accounts = parse_blackbird_json(raw)
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
    except FileNotFoundError:
        result_meta["error"] = f"blackbird not found at {bb_script}"
    except Exception as exc:
        result_meta["error"] = f"blackbird error: {exc}"
        log.error("Blackbird error for %s: %s", _redact(username or email), exc)
    return result_meta


async def execute_scan(
    *,
    usernames: List[str],
    email: Optional[str],
    deep_scan: bool,
    want_maigret: bool,
    want_blackbird: bool,
    policy_notes: List[str],
) -> Dict[str, Any]:
    """
    Run selected tools with fail-loud + degraded status.
    """
    from defaults import dedupe_accounts, score_signals

    probe = probe_tools()
    tool_results: Dict[str, Any] = {}
    warnings: List[str] = list(policy_notes)
    error_parts: List[str] = []
    all_maigret: List[Dict] = []
    all_blackbird: List[Dict] = []
    raw_maigret: Any = None
    raw_blackbird: Any = None
    quality_flags: List[str] = []

    # Cap usernames for Maigret
    mg_users = list(usernames)[:MAX_MAIGRET_USERNAMES]

    # Preflight unavailable tools
    if want_maigret and not probe["maigret"]["available"]:
        error_parts.append("maigret not available on worker")
        tool_results["maigret"] = {"ok": False, "error": "not installed", "attempted": False}
        want_maigret = False
    if want_blackbird and not probe["blackbird"]["available"]:
        error_parts.append("blackbird not available on worker")
        tool_results["blackbird"] = {"ok": False, "error": "not installed", "attempted": False}
        want_blackbird = False

    if want_maigret and not mg_users:
        error_parts.append("maigret requested but no username candidates")
        tool_results["maigret"] = {"ok": False, "error": "no username candidates", "attempted": False}
        want_maigret = False

    if want_blackbird and not mg_users and not email:
        error_parts.append("blackbird requested but no username or email")
        tool_results["blackbird"] = {"ok": False, "error": "no username or email", "attempted": False}
        want_blackbird = False

    with tempfile.TemporaryDirectory(prefix="sl_osint_") as tmpdir:
        if want_maigret:
            primary = mg_users[0]
            mg = await run_maigret(primary, deep=deep_scan, tmpdir=tmpdir)
            raw_maigret = mg.get("raw") or {}
            all_maigret.extend(mg.get("accounts") or [])
            tool_results["maigret"] = {
                "ok": bool(mg.get("ok")),
                "error": mg.get("error"),
                "warning": mg.get("warning"),
                "attempted": True,
                "accounts": len(mg.get("accounts") or []),
                "username": primary[:64],
                "quality": mg.get("quality") or {},
            }
            if not mg.get("ok"):
                error_parts.append(f"maigret: {mg.get('error') or 'failed'}")
            elif mg.get("warning"):
                warnings.append(str(mg["warning"]))
            if (mg.get("quality") or {}).get("degraded"):
                quality_flags.extend((mg.get("quality") or {}).get("reasons") or ["degraded"])

            # One alternate username only (noise control)
            if len(mg_users) > 1 and mg.get("ok"):
                alt = await run_maigret(mg_users[1], deep=False, tmpdir=tmpdir)
                existing = {(a.get("platform"), a.get("url")) for a in all_maigret}
                added = 0
                for acct in alt.get("accounts") or []:
                    key = (acct.get("platform"), acct.get("url"))
                    if key not in existing:
                        all_maigret.append(acct)
                        existing.add(key)
                        added += 1
                tool_results["maigret"]["alt_username"] = {
                    "username": mg_users[1][:64],
                    "ok": bool(alt.get("ok")),
                    "accounts_added": added,
                    "error": alt.get("error"),
                }
                tool_results["maigret"]["accounts"] = len(all_maigret)

        if want_blackbird:
            bb_user = mg_users[0] if mg_users else None
            # Email-focused: pass email when present; include username as extra signal
            bb = await run_blackbird(username=bb_user, email=email)

            raw_blackbird = bb.get("raw") or {}
            all_blackbird.extend(bb.get("accounts") or [])
            tool_results["blackbird"] = {
                "ok": bool(bb.get("ok")),
                "error": bb.get("error"),
                "warning": bb.get("warning"),
                "attempted": True,
                "accounts": len(bb.get("accounts") or []),
                "email_mode": bool(email),
            }
            if not bb.get("ok"):
                error_parts.append(f"blackbird: {bb.get('error') or 'failed'}")
            elif bb.get("warning"):
                warnings.append(str(bb["warning"]))

    all_accounts = dedupe_accounts(all_maigret + all_blackbird)
    # Cross-tool host dedupe: prefer maigret when both hit same host+path
    all_accounts = dedupe_accounts(all_accounts)

    attempted = [k for k, v in tool_results.items() if v.get("attempted")]
    succeeded = [k for k, v in tool_results.items() if v.get("ok")]
    failed_tools = [k for k, v in tool_results.items() if not v.get("ok")]

    if not (want_maigret or want_blackbird) and failed_tools:
        status = "failed"
    elif not attempted and failed_tools:
        status = "failed"
    elif attempted and not succeeded:
        status = "failed"
    elif failed_tools and succeeded:
        status = "partial" if all_accounts else "failed"
    elif quality_flags and succeeded:
        status = "degraded"
        warnings.append("Scan quality degraded: " + "; ".join(quality_flags))
    elif succeeded and not all_accounts:
        status = "complete"
        warnings.append(
            "Scan completed successfully but found 0 accounts. "
            "Try alternate usernames, email, or deep scan."
        )
    else:
        status = "complete"

    score_delta, signals = score_signals(all_accounts)
    if status == "failed":
        signals = [s for s in signals if s.get("signal_type") != "social_inactivity"]
        signals.append({
            "signal_type": "osint_scan_failed",
            "severity": "high",
            "detail": "; ".join(error_parts) or "OSINT tools failed",
            "source": "osint_worker",
        })
        score_delta = 0
    elif status == "degraded":
        signals.append({
            "signal_type": "osint_degraded",
            "severity": "medium",
            "detail": "High bot/access-denied rates — treat hits with caution.",
            "source": "osint_worker",
        })
    elif status == "partial":
        signals.append({
            "signal_type": "osint_partial",
            "severity": "medium",
            "detail": "; ".join(error_parts) or "One or more tools failed",
            "source": "osint_worker",
        })

    return {
        "status": status,
        "maigret_accounts": all_maigret,
        "blackbird_accounts": all_blackbird,
        "accounts": all_accounts,
        "total_accounts_found": len(all_accounts),
        "platforms_found": sorted({a.get("platform") for a in all_accounts if a.get("platform")}),
        "risk_signals": signals,
        "osint_risk_score": score_delta,
        "tool_results": tool_results,
        "warnings": warnings,
        "error": "; ".join(error_parts) if error_parts else None,
        "raw_maigret_json": raw_maigret,
        "raw_blackbird_json": raw_blackbird,
        "policy": {
            "deep_scan": deep_scan,
            "maigret": want_maigret or "maigret" in tool_results,
            "blackbird": want_blackbird or "blackbird" in tool_results,
            "notes": policy_notes,
        },
        # Advisory only — dashboard must not auto-apply to bond risk
        "risk_is_advisory": True,
    }
