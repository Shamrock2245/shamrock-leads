"""
CLI runners for Maigret, Sherlock, Blackbird, SpiderFoot — osint-worker v2
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
    SHERLOCK_TIMEOUT,
    SPIDERFOOT_TIMEOUT,
    assess_maigret_quality,
    maigret_site_args,
)

log = logging.getLogger("osint_worker.runners")
PYTHON_CMD = shutil.which("python3") or shutil.which("python") or "python3"


def _redact(value: Optional[str]) -> str:
    if not value:
        return "[empty]"
    return f"[redacted:{hashlib.sha256(value.encode()).hexdigest()[:8]}]"


# ══════════════════════════════════════════════════════════════════════════════
# Tool Resolution
# ══════════════════════════════════════════════════════════════════════════════

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


def resolve_sherlock_cmd() -> Optional[str]:
    """Resolve sherlock CLI binary or python module."""
    candidates = [
        os.getenv("SHERLOCK_PATH", "").strip(),
        shutil.which("sherlock") or "",
        "/usr/local/bin/sherlock",
        "/usr/bin/sherlock",
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
        if c and os.path.isfile(c):
            return c
    try:
        import importlib.util
        if importlib.util.find_spec("sherlock_project") is not None:
            return "python-module"
        if importlib.util.find_spec("sherlock") is not None:
            return "python-module"
    except Exception:
        pass
    return None


def resolve_spiderfoot() -> Optional[str]:
    """Resolve SpiderFoot CLI (sf.py or sfcli.py)."""
    candidates = [
        os.getenv("SPIDERFOOT_PATH", "").strip(),
        shutil.which("sf") or "",
        shutil.which("spiderfoot") or "",
        "/opt/spiderfoot/sf.py",
        "/opt/spiderfoot/sfcli.py",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    try:
        import importlib.util
        if importlib.util.find_spec("spiderfoot") is not None:
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


# ══════════════════════════════════════════════════════════════════════════════
# Tool Probe
# ══════════════════════════════════════════════════════════════════════════════

def probe_tools() -> Dict[str, Any]:
    """Probe all 4 engines for availability."""
    maigret_cmd = resolve_maigret_cmd()
    sherlock_cmd = resolve_sherlock_cmd()
    spiderfoot_cmd = resolve_spiderfoot()
    bb_dir, bb_script = resolve_blackbird()

    # Maigret
    maigret_ok = False
    maigret_version = None
    maigret_error = None
    maigret_path = None

    if maigret_cmd == "python-module":
        try:
            import maigret as _m
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
            r = subprocess.run([maigret_cmd, "--version"], capture_output=True, text=True, timeout=8)
            maigret_version = (r.stdout or r.stderr or "").strip().split("\n")[0][:80]
        except Exception as e:
            maigret_version = f"unknown ({e})"
    else:
        maigret_error = "not found — install with: pip install maigret"

    # Sherlock
    sherlock_ok = False
    sherlock_version = None
    sherlock_error = None
    sherlock_path = None

    if sherlock_cmd == "python-module":
        sherlock_ok = True
        sherlock_path = f"{PYTHON_CMD} -m sherlock_project"
        sherlock_version = "installed (module)"
    elif sherlock_cmd:
        sherlock_ok = True
        sherlock_path = sherlock_cmd
        try:
            import subprocess
            r = subprocess.run([sherlock_cmd, "--version"], capture_output=True, text=True, timeout=8)
            sherlock_version = (r.stdout or r.stderr or "").strip().split("\n")[0][:80]
        except Exception as e:
            sherlock_version = f"unknown ({e})"
    else:
        sherlock_error = "not found — install with: pip install sherlock-project"

    # SpiderFoot
    spiderfoot_ok = False
    spiderfoot_version = None
    spiderfoot_error = None
    spiderfoot_path = None

    if spiderfoot_cmd == "python-module":
        spiderfoot_ok = True
        spiderfoot_path = f"{PYTHON_CMD} -m spiderfoot"
        spiderfoot_version = "installed (module)"
    elif spiderfoot_cmd:
        spiderfoot_ok = True
        spiderfoot_path = spiderfoot_cmd
        spiderfoot_version = "installed"
    else:
        spiderfoot_error = "not found — install with: pip install spiderfoot"

    # Blackbird
    blackbird_ok = bool(bb_script and os.path.isfile(bb_script))
    blackbird_error = None
    if blackbird_ok:
        wmn = os.path.join(bb_dir or "", "data", "wmn-data.json")
        if not os.path.isfile(wmn) or os.path.getsize(wmn) < 1000:
            blackbird_ok = False
            blackbird_error = "missing data/wmn-data.json"
    else:
        blackbird_error = "not found — clone blackbird to /opt/blackbird"

    return {
        "maigret": {
            "available": maigret_ok,
            "path": maigret_path or "not found",
            "version": maigret_version,
            "error": maigret_error,
        },
        "sherlock": {
            "available": sherlock_ok,
            "path": sherlock_path or "not found",
            "version": sherlock_version,
            "error": sherlock_error,
        },
        "blackbird": {
            "available": blackbird_ok,
            "path": bb_script or "not found",
            "dir": bb_dir,
            "error": blackbird_error,
        },
        "spiderfoot": {
            "available": spiderfoot_ok,
            "path": spiderfoot_path or "not found",
            "version": spiderfoot_version,
            "error": spiderfoot_error,
        },
        "ready_for_scans": maigret_ok or sherlock_ok or blackbird_ok or spiderfoot_ok,
        "worker": True,
        "version": "2.0.0",
        "defaults": {
            "maigret_default": True,
            "sherlock_default": True,
            "blackbird_default": False,
            "spiderfoot_default": False,
            "blackbird_on_email": True,
            "spiderfoot_on_phone": True,
            "no_recursion": MAIGRET_NO_RECURSION,
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# Parsers
# ══════════════════════════════════════════════════════════════════════════════

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
            st = str(status_blob.get("status") or status_blob.get("id") or "").lower().strip()
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
            "category": _categorize_platform(site_name),
            "relevance": "unreviewed",
        })
    return accounts


def parse_sherlock_json(raw: Any) -> List[Dict]:
    """Parse Sherlock JSON output (dict of site_name → {url, status, ...})."""
    accounts: List[Dict] = []
    if not raw:
        return accounts
    if isinstance(raw, list):
        # Some versions output a list
        for item in raw:
            if not isinstance(item, dict):
                continue
            st = str(item.get("status") or "").lower()
            if st not in ("claimed", "found"):
                continue
            accounts.append({
                "platform": item.get("site", "Unknown"),
                "url": item.get("url_user") or item.get("url", ""),
                "username": item.get("username", ""),
                "profile_data": {},
                "source": "sherlock",
                "confidence": "found",
                "category": _categorize_platform(item.get("site", "")),
                "relevance": "unreviewed",
            })
        return accounts

    if not isinstance(raw, dict):
        return accounts

    for site_name, site_data in raw.items():
        if not isinstance(site_data, dict):
            continue
        st = str(site_data.get("status") or "").lower()
        if st not in ("claimed", "found"):
            continue
        url = site_data.get("url_user") or site_data.get("url") or ""
        accounts.append({
            "platform": str(site_name),
            "url": url,
            "username": site_data.get("username", ""),
            "profile_data": site_data.get("response_time_s", {}),
            "source": "sherlock",
            "confidence": "found",
            "category": _categorize_platform(site_name),
            "relevance": "unreviewed",
        })
    return accounts


def parse_spiderfoot_json(raw: Any) -> tuple[List[Dict], List[Dict]]:
    """
    Parse SpiderFoot JSON output.
    Returns (accounts, entities).
    SpiderFoot outputs correlations as list of dicts with keys:
      type, data, module, source, confidence
    """
    accounts: List[Dict] = []
    entities: List[Dict] = []

    if not raw:
        return accounts, entities

    results = raw if isinstance(raw, list) else raw.get("results", []) if isinstance(raw, dict) else []

    # SpiderFoot event types that map to social accounts
    social_types = {
        "SOCIAL_MEDIA", "ACCOUNT_EXTERNAL_OWNED",
        "SOCIAL_MEDIA - Profile", "USERNAME",
    }
    # Entity types
    entity_type_map = {
        "EMAILADDR": "email",
        "EMAIL_ADDRESS": "email",
        "PHONE_NUMBER": "phone",
        "PHONE": "phone",
        "PHYSICAL_ADDRESS": "address",
        "GEOINFO": "address",
        "HUMAN_NAME": "name",
        "DOMAIN_NAME": "domain",
        "IP_ADDRESS": "ip",
        "INTERNET_NAME": "domain",
        "AFFILIATE_DOMAIN_NAME": "domain",
        "COMPANY_NAME": "organization",
        "ORGANIZATION": "organization",
    }

    for item in results:
        if not isinstance(item, dict):
            continue
        event_type = item.get("type") or item.get("event_type") or ""
        data = item.get("data") or item.get("value") or ""
        module = item.get("module") or item.get("source_module") or ""
        confidence = item.get("confidence") or "medium"

        if event_type in social_types or "SOCIAL" in event_type.upper():
            # Extract URL if present
            url = data if data.startswith("http") else ""
            platform = _extract_platform_from_url(url) if url else event_type
            accounts.append({
                "platform": platform,
                "url": url,
                "username": "",
                "profile_data": {"module": module},
                "source": "spiderfoot",
                "confidence": "found" if confidence in ("high", "100") else "likely",
                "category": "social",
                "relevance": "unreviewed",
            })
        elif event_type.upper() in entity_type_map or any(
            k in event_type.upper() for k in entity_type_map
        ):
            etype = entity_type_map.get(event_type.upper(), "other")
            if etype == "other":
                for k, v in entity_type_map.items():
                    if k in event_type.upper():
                        etype = v
                        break
            entities.append({
                "type": etype,
                "value": str(data)[:500],
                "source": "spiderfoot",
                "module": module,
                "confidence": str(confidence).lower() if confidence else "medium",
                "context": event_type,
                "relevance": "unreviewed",
            })

    return accounts, entities


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
            "category": _categorize_platform(str(platform)),
            "relevance": "unreviewed",
        })
    return accounts


def _categorize_platform(name: str) -> str:
    """Categorize a platform name into a category."""
    name_lower = name.lower()
    social = {"facebook", "twitter", "instagram", "tiktok", "snapchat", "linkedin", "pinterest", "tumblr", "mastodon", "threads", "x"}
    forum = {"reddit", "quora", "stackoverflow", "hackernews", "4chan", "discord"}
    dating = {"tinder", "bumble", "okcupid", "pof", "hinge", "match"}
    professional = {"linkedin", "github", "gitlab", "behance", "dribbble", "upwork", "fiverr"}

    if any(s in name_lower for s in social):
        return "social"
    if any(s in name_lower for s in forum):
        return "forum"
    if any(s in name_lower for s in dating):
        return "dating"
    if any(s in name_lower for s in professional):
        return "professional"
    return "other"


def _extract_platform_from_url(url: str) -> str:
    """Extract platform name from URL."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower().removeprefix("www.")
        parts = host.split(".")
        if len(parts) >= 2:
            return parts[-2].capitalize()
    except Exception:
        pass
    return "Unknown"


# ══════════════════════════════════════════════════════════════════════════════
# Engine Runners
# ══════════════════════════════════════════════════════════════════════════════

async def run_maigret(
    username: str,
    deep: bool = False,
    tmpdir: str = "",
) -> Dict[str, Any]:
    """Run Maigret for a single username."""
    result_meta: Dict[str, Any] = {
        "tool": "maigret", "ok": False, "error": None,
        "warning": None, "raw": {}, "accounts": [], "quality": {},
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
        import maigret as _maigret_mod
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
        username, "-J", "simple", "-fo", out_dir,
        "--timeout", str(MAIGRET_SITE_TIMEOUT),
        "--no-progressbar", "--no-color",
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
            env=child_env, cwd=out_dir,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=MAIGRET_TIMEOUT)
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
            result_meta["error"] = f"maigret no JSON report (exit {proc.returncode})"
            return result_meta

        with open(report_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        accounts = parse_maigret_json(raw)
        result_meta.update({"ok": True, "raw": raw, "accounts": accounts})
        if quality.get("degraded"):
            result_meta["warning"] = "maigret quality degraded"
        return result_meta

    except asyncio.TimeoutError:
        result_meta["error"] = f"maigret timed out after {MAIGRET_TIMEOUT}s"
    except FileNotFoundError:
        result_meta["error"] = f"maigret executable not found ({maigret_cmd})"
    except Exception as exc:
        result_meta["error"] = f"maigret error: {exc}"
    return result_meta


async def run_sherlock(
    username: str,
    deep: bool = False,
    tmpdir: str = "",
) -> Dict[str, Any]:
    """Run Sherlock for a single username."""
    result_meta: Dict[str, Any] = {
        "tool": "sherlock", "ok": False, "error": None,
        "warning": None, "raw": {}, "accounts": [],
    }
    if not username or len(username) < 2:
        result_meta["error"] = "username too short"
        return result_meta

    sherlock_cmd = resolve_sherlock_cmd()
    if not sherlock_cmd:
        result_meta["error"] = "sherlock not installed"
        return result_meta

    out_dir = tmpdir or tempfile.mkdtemp(prefix="sherlock_")
    output_file = os.path.join(out_dir, f"sherlock_{username}.json")

    if sherlock_cmd == "python-module":
        cmd = [PYTHON_CMD, "-m", "sherlock_project"]
    else:
        cmd = [sherlock_cmd]

    cmd += [
        username,
        "--output", output_file,
        "--folderoutput", out_dir,
        "--json", output_file,
        "--no-color",
        "--timeout", "15",
    ]

    if not deep:
        # Sherlock doesn't have a top-sites flag, but we can limit via timeout
        cmd += ["--timeout", "10"]

    log.info("Sherlock scan for %s deep=%s", _redact(username), deep)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=out_dir,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=SHERLOCK_TIMEOUT)
        stderr_s = (stderr or b"").decode("utf-8", errors="replace")
        stdout_s = (stdout or b"").decode("utf-8", errors="replace")

        # Find JSON output
        json_path = None
        if os.path.isfile(output_file):
            json_path = output_file
        else:
            # Sherlock may output to different filename patterns
            for fn in os.listdir(out_dir):
                if fn.endswith(".json"):
                    json_path = os.path.join(out_dir, fn)
                    break

        if not json_path:
            # Try parsing stdout as JSON
            try:
                raw = json.loads(stdout_s) if stdout_s.strip().startswith(("{", "[")) else {}
            except Exception:
                raw = {}
            if not raw:
                result_meta["error"] = f"sherlock no JSON output (exit {proc.returncode})"
                return result_meta
        else:
            with open(json_path, "r", encoding="utf-8") as f:
                raw = json.load(f)

        accounts = parse_sherlock_json(raw)
        result_meta.update({"ok": True, "raw": raw, "accounts": accounts})
        if not accounts:
            result_meta["warning"] = "sherlock ran but found 0 accounts"
        return result_meta

    except asyncio.TimeoutError:
        result_meta["error"] = f"sherlock timed out after {SHERLOCK_TIMEOUT}s"
    except FileNotFoundError:
        result_meta["error"] = f"sherlock executable not found ({sherlock_cmd})"
    except Exception as exc:
        result_meta["error"] = f"sherlock error: {exc}"
    return result_meta


async def run_spiderfoot(
    *,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    full_name: Optional[str] = None,
    username: Optional[str] = None,
    deep: bool = False,
) -> Dict[str, Any]:
    """Run SpiderFoot CLI scan."""
    result_meta: Dict[str, Any] = {
        "tool": "spiderfoot", "ok": False, "error": None,
        "warning": None, "raw": {}, "accounts": [], "entities": [],
    }

    if not any([email, phone, full_name, username]):
        result_meta["error"] = "no target provided for SpiderFoot"
        return result_meta

    sf_cmd = resolve_spiderfoot()
    if not sf_cmd:
        result_meta["error"] = "spiderfoot not installed"
        return result_meta

    out_dir = tempfile.mkdtemp(prefix="spiderfoot_")
    output_file = os.path.join(out_dir, "sf_results.json")

    # Build target — SpiderFoot accepts various target types
    target = email or phone or full_name or username or ""

    if sf_cmd == "python-module":
        cmd = [PYTHON_CMD, "-m", "spiderfoot"]
    else:
        cmd = [PYTHON_CMD, sf_cmd] if sf_cmd.endswith(".py") else [sf_cmd]

    # SpiderFoot CLI mode: sf.py -s <target> -o json -q
    cmd += [
        "-s", target,
        "-o", "json",
        "-q",  # quiet mode
    ]

    # Module selection based on depth
    if not deep:
        # Quick scan: use only passive/safe modules
        modules = [
            "sfp_accounts", "sfp_emailformat", "sfp_hunter",
            "sfp_fullcontact", "sfp_social_media",
            "sfp_names", "sfp_phone",
        ]
        cmd += ["-m", ",".join(modules)]

    log.info("SpiderFoot scan for target=%s deep=%s", _redact(target), deep)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=out_dir,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=SPIDERFOOT_TIMEOUT)
        stdout_s = (stdout or b"").decode("utf-8", errors="replace")
        stderr_s = (stderr or b"").decode("utf-8", errors="replace")

        # Parse JSON output from stdout
        raw = {}
        try:
            if stdout_s.strip().startswith(("[", "{")):
                raw = json.loads(stdout_s)
            elif os.path.isfile(output_file):
                with open(output_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
        except json.JSONDecodeError:
            # Try line-by-line JSON (JSONL format)
            results = []
            for line in stdout_s.strip().split("\n"):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        results.append(json.loads(line))
                    except Exception:
                        pass
            if results:
                raw = {"results": results}

        if not raw:
            result_meta["error"] = f"spiderfoot no parseable output (exit {proc.returncode})"
            result_meta["stderr_tail"] = stderr_s[-400:]
            return result_meta

        accounts, entities = parse_spiderfoot_json(raw)
        result_meta.update({
            "ok": True,
            "raw": raw if isinstance(raw, dict) else {"results": raw},
            "accounts": accounts,
            "entities": entities,
        })
        if not accounts and not entities:
            result_meta["warning"] = "spiderfoot ran but found 0 results"
        return result_meta

    except asyncio.TimeoutError:
        result_meta["error"] = f"spiderfoot timed out after {SPIDERFOOT_TIMEOUT}s"
    except FileNotFoundError:
        result_meta["error"] = f"spiderfoot not found at {sf_cmd}"
    except Exception as exc:
        result_meta["error"] = f"spiderfoot error: {exc}"
    return result_meta


async def run_blackbird(
    username: Optional[str] = None,
    email: Optional[str] = None,
) -> Dict[str, Any]:
    """Run Blackbird for username/email."""
    result_meta: Dict[str, Any] = {
        "tool": "blackbird", "ok": False, "error": None,
        "warning": None, "raw": {}, "accounts": [],
    }
    if not username and not email:
        result_meta["error"] = "username or email required"
        return result_meta

    bb_dir, bb_script = resolve_blackbird()
    if not bb_script or not bb_dir:
        result_meta["error"] = "blackbird not installed"
        return result_meta

    wmn_path = os.path.join(bb_dir, "data", "wmn-data.json")
    if not os.path.isfile(wmn_path) or os.path.getsize(wmn_path) < 1000:
        result_meta["error"] = "blackbird missing data/wmn-data.json"
        return result_meta

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
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=BLACKBIRD_TIMEOUT)
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
        if not after:
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
                result_meta["error"] = f"blackbird no JSON results (exit {proc.returncode})"
                return result_meta

        accounts = parse_blackbird_json(raw)
        result_meta.update({
            "ok": True,
            "raw": raw if isinstance(raw, dict) else {"results": raw},
            "accounts": accounts,
        })
        if not accounts:
            result_meta["warning"] = "blackbird ran but found 0 accounts"
        return result_meta

    except asyncio.TimeoutError:
        result_meta["error"] = f"blackbird timed out after {BLACKBIRD_TIMEOUT}s"
    except FileNotFoundError:
        result_meta["error"] = f"blackbird not found at {bb_script}"
    except Exception as exc:
        result_meta["error"] = f"blackbird error: {exc}"
    return result_meta


# ══════════════════════════════════════════════════════════════════════════════
# Orchestrators
# ══════════════════════════════════════════════════════════════════════════════

async def execute_scan(
    *,
    usernames: List[str],
    email: Optional[str],
    deep_scan: bool,
    want_maigret: bool,
    want_blackbird: bool,
    policy_notes: List[str],
) -> Dict[str, Any]:
    """Legacy v1 scan orchestrator (Maigret + Blackbird only)."""
    from defaults import dedupe_accounts, score_signals

    probe = probe_tools()
    tool_results: Dict[str, Any] = {}
    warnings: List[str] = list(policy_notes)
    error_parts: List[str] = []
    all_maigret: List[Dict] = []
    all_blackbird: List[Dict] = []
    raw_maigret: Any = None
    raw_blackbird: Any = None

    mg_users = list(usernames)[:MAX_MAIGRET_USERNAMES]

    if want_maigret and not probe["maigret"]["available"]:
        error_parts.append("maigret not available")
        want_maigret = False
    if want_blackbird and not probe["blackbird"]["available"]:
        error_parts.append("blackbird not available")
        want_blackbird = False

    with tempfile.TemporaryDirectory(prefix="sl_osint_") as tmpdir:
        if want_maigret and mg_users:
            mg = await run_maigret(mg_users[0], deep=deep_scan, tmpdir=tmpdir)
            raw_maigret = mg.get("raw") or {}
            all_maigret.extend(mg.get("accounts") or [])
            tool_results["maigret"] = {"ok": bool(mg.get("ok")), "accounts": len(mg.get("accounts") or [])}
            if not mg.get("ok"):
                error_parts.append(f"maigret: {mg.get('error')}")

        if want_blackbird:
            bb_user = mg_users[0] if mg_users else None
            bb = await run_blackbird(username=bb_user, email=email)
            raw_blackbird = bb.get("raw") or {}
            all_blackbird.extend(bb.get("accounts") or [])
            tool_results["blackbird"] = {"ok": bool(bb.get("ok")), "accounts": len(bb.get("accounts") or [])}
            if not bb.get("ok"):
                error_parts.append(f"blackbird: {bb.get('error')}")

    all_accounts = dedupe_accounts(all_maigret + all_blackbird)
    status = "complete" if not error_parts else ("partial" if all_accounts else "failed")
    score_delta, signals = score_signals(all_accounts)

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
        "policy": {"maigret": want_maigret, "blackbird": want_blackbird, "notes": policy_notes},
        "risk_is_advisory": True,
    }


async def execute_scan_v2(
    *,
    usernames: List[str],
    email: Optional[str],
    phone: Optional[str],
    full_name: Optional[str],
    deep_scan: bool,
    engines: List[str],
) -> Dict[str, Any]:
    """
    v2 multi-engine scan orchestrator.
    Runs requested engines concurrently, returns unified result.
    """
    from defaults import dedupe_accounts, score_signals

    probe = probe_tools()
    progress: Dict[str, Dict] = {}
    tool_results: Dict[str, Any] = {}
    warnings: List[str] = []
    error_parts: List[str] = []
    all_accounts: List[Dict] = []
    all_entities: List[Dict] = []
    raw_outputs: Dict[str, Any] = {}

    mg_users = list(usernames)[:MAX_MAIGRET_USERNAMES]

    # Initialize progress
    for engine in engines:
        progress[engine] = {
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "accounts_found": 0,
            "entities_found": 0,
            "error": None,
            "warning": None,
        }

    # Preflight checks
    active_engines = []
    for engine in engines:
        if engine in probe and not probe[engine].get("available"):
            progress[engine]["status"] = "failed"
            progress[engine]["error"] = f"{engine} not available on worker"
            error_parts.append(f"{engine} not available")
            tool_results[engine] = {"ok": False, "error": "not installed"}
        else:
            active_engines.append(engine)

    if not active_engines:
        return {
            "status": "failed",
            "accounts": [],
            "entities": [],
            "total_accounts": 0,
            "total_entities": 0,
            "platforms_found": [],
            "risk_signals": [{"signal_type": "osint_scan_failed", "severity": "high", "detail": "No engines available", "source": "osint_worker"}],
            "osint_risk_score": 0,
            "progress": progress,
            "raw_outputs": {},
            "tool_results": tool_results,
            "warnings": warnings,
            "error": "; ".join(error_parts),
        }

    # Run engines concurrently
    tasks = []
    task_map = []

    with tempfile.TemporaryDirectory(prefix="sl_osint_v2_") as tmpdir:
        for engine in active_engines:
            from datetime import datetime, timezone
            progress[engine]["status"] = "running"
            progress[engine]["started_at"] = datetime.now(timezone.utc).isoformat()

            if engine == "maigret" and mg_users:
                tasks.append(run_maigret(mg_users[0], deep=deep_scan, tmpdir=tmpdir))
                task_map.append(engine)
            elif engine == "sherlock" and mg_users:
                tasks.append(run_sherlock(mg_users[0], deep=deep_scan, tmpdir=tmpdir))
                task_map.append(engine)
            elif engine == "blackbird":
                bb_user = mg_users[0] if mg_users else None
                tasks.append(run_blackbird(username=bb_user, email=email))
                task_map.append(engine)
            elif engine == "spiderfoot":
                tasks.append(run_spiderfoot(
                    email=email, phone=phone, full_name=full_name,
                    username=mg_users[0] if mg_users else None,
                    deep=deep_scan,
                ))
                task_map.append(engine)
            else:
                progress[engine]["status"] = "skipped"
                progress[engine]["error"] = f"No valid input for {engine}"

        # Execute all tasks concurrently
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        else:
            results = []

    # Process results
    from datetime import datetime, timezone
    for i, result in enumerate(results):
        engine = task_map[i]
        now_iso = datetime.now(timezone.utc).isoformat()
        progress[engine]["completed_at"] = now_iso

        if isinstance(result, Exception):
            progress[engine]["status"] = "failed"
            progress[engine]["error"] = str(result)
            error_parts.append(f"{engine}: {result}")
            tool_results[engine] = {"ok": False, "error": str(result)}
            continue

        if not isinstance(result, dict):
            progress[engine]["status"] = "failed"
            progress[engine]["error"] = "unexpected result type"
            continue

        ok = result.get("ok", False)
        accounts = result.get("accounts") or []
        entities = result.get("entities") or []

        if ok:
            progress[engine]["status"] = "completed"
            progress[engine]["accounts_found"] = len(accounts)
            progress[engine]["entities_found"] = len(entities)
            if result.get("warning"):
                progress[engine]["warning"] = result["warning"]
                warnings.append(f"{engine}: {result['warning']}")
        else:
            progress[engine]["status"] = "failed"
            progress[engine]["error"] = result.get("error")
            error_parts.append(f"{engine}: {result.get('error')}")

        tool_results[engine] = {
            "ok": ok,
            "accounts": len(accounts),
            "entities": len(entities),
            "error": result.get("error"),
            "warning": result.get("warning"),
        }

        all_accounts.extend(accounts)
        all_entities.extend(entities)
        raw_outputs[engine] = result.get("raw") or {}

    # Deduplicate accounts
    all_accounts = dedupe_accounts(all_accounts)

    # Determine overall status
    succeeded = [e for e in active_engines if progress.get(e, {}).get("status") == "completed"]
    failed = [e for e in active_engines if progress.get(e, {}).get("status") == "failed"]

    if not succeeded and failed:
        status = "failed"
    elif succeeded and failed:
        status = "partial"
    elif succeeded:
        status = "completed"
    else:
        status = "failed"

    # Score
    score_delta, signals = score_signals(all_accounts)
    if status == "failed":
        signals.append({
            "signal_type": "osint_scan_failed",
            "severity": "high",
            "detail": "; ".join(error_parts) or "All engines failed",
            "source": "osint_worker",
        })
        score_delta = 0

    return {
        "status": status,
        "accounts": all_accounts,
        "entities": all_entities,
        "total_accounts": len(all_accounts),
        "total_entities": len(all_entities),
        "platforms_found": sorted({a.get("platform") for a in all_accounts if a.get("platform")}),
        "risk_signals": signals,
        "osint_risk_score": score_delta,
        "progress": progress,
        "raw_outputs": raw_outputs,
        "tool_results": tool_results,
        "warnings": warnings,
        "error": "; ".join(error_parts) if error_parts else None,
        "risk_is_advisory": True,
    }
