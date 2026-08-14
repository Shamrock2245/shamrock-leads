"""
CLI runners for Maigret, Sherlock, Blackbird, SpiderFoot, Ignorant, Holehe, Toutatis
— osint-worker v2. Writable filesystem assumed (not read-only dashboard rootfs).
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx

from defaults import (
    BLACKBIRD_TIMEOUT,
    HIBF_BASE_URL,
    HIBF_TIMEOUT,
    HOLEHE_TIMEOUT,
    IGNORANT_TIMEOUT,
    MAIGRET_NO_AUTOUPDATE,
    MAIGRET_NO_RECURSION,
    MAIGRET_SITE_TIMEOUT,
    MAIGRET_TIMEOUT,
    MAX_MAIGRET_USERNAMES,
    MAX_TOUTATIS_USERNAMES,
    SHERLOCK_TIMEOUT,
    SPIDERFOOT_TIMEOUT,
    TOOKIE_TIMEOUT,
    TOUTATIS_TIMEOUT,
    assess_maigret_quality,
    dedupe_accounts,
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


def resolve_tookie_cmd() -> Optional[str]:
    """Resolve Tookie-OSINT CLI binary or python script entrypoint."""
    candidates = [
        os.getenv("TOOKIE_PATH", "").strip(),
        "/opt/tookie-osint/brib.py",
        "/opt/tookie-osint/tookie-osint.py",
        shutil.which("tookie-osint") or "",
        shutil.which("tookie") or "",
        "/usr/local/bin/tookie-osint",
        "/usr/bin/tookie-osint",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    try:
        import importlib.util
        if importlib.util.find_spec("tookie") is not None:
            return "python-module:tookie"
        if importlib.util.find_spec("tookie_osint") is not None:
            return "python-module:tookie_osint"
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
            return "python-module:sherlock_project"
        if importlib.util.find_spec("sherlock") is not None:
            return "python-module:sherlock"
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


def resolve_ignorant() -> Optional[str]:
    """Resolve ignorant package / CLI for phone registration checks."""
    candidates = [
        os.getenv("IGNORANT_PATH", "").strip(),
        shutil.which("ignorant") or "",
        "/usr/local/bin/ignorant",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    try:
        import importlib.util

        if importlib.util.find_spec("ignorant") is not None:
            return "python-module"
    except Exception:
        pass
    return None


def resolve_holehe() -> Optional[str]:
    """Resolve holehe package / CLI for email registration checks."""
    candidates = [
        os.getenv("HOLEHE_PATH", "").strip(),
        shutil.which("holehe") or "",
        "/usr/local/bin/holehe",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    try:
        import importlib.util

        if importlib.util.find_spec("holehe") is not None:
            return "python-module"
    except Exception:
        pass
    return None


def resolve_toutatis() -> Optional[str]:
    """Resolve toutatis package / CLI for Instagram username enrichment."""
    candidates = [
        os.getenv("TOUTATIS_PATH", "").strip(),
        shutil.which("toutatis") or "",
        "/usr/local/bin/toutatis",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    try:
        import importlib.util

        if importlib.util.find_spec("toutatis") is not None:
            return "python-module"
    except Exception:
        pass
    return None


def resolve_instaloader() -> Optional[str]:
    """Resolve instaloader package / CLI for Instagram media & profile metadata."""
    candidates = [
        os.getenv("INSTALOADER_PATH", "").strip(),
        shutil.which("instaloader") or "",
        "/usr/local/bin/instaloader",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    try:
        import importlib.util

        if importlib.util.find_spec("instaloader") is not None:
            return "python-module"
    except Exception:
        pass
    return None


def resolve_exiftool() -> Optional[str]:
    """Resolve exiftool CLI binary for image metadata & GPS extraction."""
    candidates = [
        os.getenv("EXIFTOOL_PATH", "").strip(),
        shutil.which("exiftool") or "",
        "/usr/bin/exiftool",
        "/usr/local/bin/exiftool",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def get_instagram_session_id() -> str:
    """
    Instagram sessionid cookie for Toutatis / Instaloader.

    Prefer ``INSTAGRAM_SESSION_ID`` (clear ops name); accept ``TOUTATIS_SESSION_ID``
    as alias. Chrome DevTools often copies the value URL-encoded (``%3A``);
    decode so Instagram APIs see real colons. Never log the raw value.
    """
    from urllib.parse import unquote

    raw = (
        os.getenv("INSTAGRAM_SESSION_ID", "").strip()
        or os.getenv("TOUTATIS_SESSION_ID", "").strip()
    )
    if not raw:
        return ""
    # Strip wrapping quotes from .env editors
    if (raw[0], raw[-1]) in {('"', '"'), ("'", "'")}:
        raw = raw[1:-1]
    return unquote(raw).strip()


def parse_phone_for_ignorant(phone: str) -> Optional[Tuple[str, str]]:
    """
    Split a phone string into (country_code, national_number) for ignorant.

    Defaults to US/Canada (+1) when given 10-digit NANP numbers — correct for
    Shamrock FL bail-bond intake. Returns None if unusable.
    """
    raw = (phone or "").strip()
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("00"):
        digits = digits[2:]
    if not digits or len(digits) < 7:
        return None

    # NANP
    if len(digits) == 11 and digits.startswith("1"):
        return "1", digits[1:]
    if len(digits) == 10:
        return "1", digits

    # International: peel 1–3 digit country code when remainder looks national
    if len(digits) > 10:
        if digits.startswith("1") and len(digits) >= 11:
            return "1", digits[1:11]
        for cc_len in (1, 2, 3):
            cc = digits[:cc_len]
            rest = digits[cc_len:]
            if 6 <= len(rest) <= 12 and not rest.startswith("0"):
                return cc, rest

    if 7 <= len(digits) <= 15:
        return "1", digits
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Tool Probe
# ══════════════════════════════════════════════════════════════════════════════

def probe_tools() -> Dict[str, Any]:
    """Probe all engines for availability (incl. Tookie-OSINT + Ignorant phone checks)."""
    maigret_cmd = resolve_maigret_cmd()
    tookie_cmd = resolve_tookie_cmd()
    sherlock_cmd = resolve_sherlock_cmd()
    spiderfoot_cmd = resolve_spiderfoot()
    bb_dir, bb_script = resolve_blackbird()
    ignorant_cmd = resolve_ignorant()
    toutatis_cmd = resolve_toutatis()
    ig_session = get_instagram_session_id()

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

    # Tookie-OSINT (v4)
    tookie_ok = False
    tookie_version = None
    tookie_error = None
    tookie_path = None

    if tookie_cmd and (tookie_cmd.endswith(".py") or os.path.isfile(tookie_cmd)):
        tookie_ok = True
        tookie_path = tookie_cmd
        tookie_version = "v4 (installed)"
    elif tookie_cmd and tookie_cmd.startswith("python-module"):
        tookie_ok = True
        mod_name = tookie_cmd.split(":", 1)[1] if ":" in tookie_cmd else "tookie"
        tookie_path = f"{PYTHON_CMD} -m {mod_name}"
        tookie_version = f"installed (module: {mod_name})"
    else:
        tookie_error = "not found — clone tookie-osint to /opt/tookie-osint"

    # Sherlock
    sherlock_ok = False
    sherlock_version = None
    sherlock_error = None
    sherlock_path = None

    if sherlock_cmd and sherlock_cmd.startswith("python-module"):
        sherlock_ok = True
        mod_name = sherlock_cmd.split(":", 1)[1] if ":" in sherlock_cmd else "sherlock_project"
        sherlock_path = f"{PYTHON_CMD} -m {mod_name}"
        sherlock_version = f"installed (module: {mod_name})"
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

    # Ignorant (phone → IG / Snap / Amazon registration)
    ignorant_ok = False
    ignorant_version = None
    ignorant_error = None
    ignorant_path = None
    if ignorant_cmd == "python-module":
        try:
            from ignorant.core import __version__ as _ig_ver  # type: ignore

            ignorant_ok = True
            ignorant_version = str(_ig_ver)
            ignorant_path = f"{PYTHON_CMD} -m ignorant (API)"
        except Exception as e:
            try:
                import ignorant as _ig  # noqa: F401

                ignorant_ok = True
                ignorant_version = getattr(_ig, "__version__", "installed")
                ignorant_path = f"{PYTHON_CMD} -m ignorant"
            except Exception as e2:
                ignorant_error = str(e2)[:200]
    elif ignorant_cmd:
        ignorant_ok = True
        ignorant_path = ignorant_cmd
        ignorant_version = "cli"
    else:
        ignorant_error = "not found — install with: pip install ignorant"

    # Holehe (email → 120+ site registration, same family as Ignorant)
    holehe_cmd = resolve_holehe()
    holehe_ok = False
    holehe_version = None
    holehe_error = None
    holehe_path = None
    if holehe_cmd == "python-module":
        try:
            import holehe as _hh  # noqa: F401

            holehe_ok = True
            holehe_version = getattr(_hh, "__version__", "installed")
            holehe_path = f"{PYTHON_CMD} -m holehe"
        except Exception as e:
            holehe_error = str(e)[:200]
    elif holehe_cmd:
        holehe_ok = True
        holehe_path = holehe_cmd
        holehe_version = "cli"
    else:
        holehe_error = "not found — install with: pip install holehe"

    # Toutatis (Instagram username enrichment via session cookie)
    toutatis_pkg = False
    toutatis_version = None
    toutatis_error = None
    toutatis_path = None
    if toutatis_cmd == "python-module":
        try:
            import toutatis as _tt  # noqa: F401

            toutatis_pkg = True
            toutatis_version = getattr(_tt, "__version__", "installed")
            toutatis_path = f"{PYTHON_CMD} -m toutatis"
        except Exception as e:
            toutatis_error = str(e)[:200]
    elif toutatis_cmd:
        toutatis_pkg = True
        toutatis_path = toutatis_cmd
        toutatis_version = "cli"
    else:
        toutatis_error = "not found — install with: pip install toutatis"

    session_configured = bool(ig_session)
    # Runnable only when package + session cookie both present
    toutatis_ok = toutatis_pkg and session_configured
    if toutatis_pkg and not session_configured:
        toutatis_error = (
            "set INSTAGRAM_SESSION_ID (or TOUTATIS_SESSION_ID) on osint-worker — "
            "browser cookie 'sessionid' from an Instagram login"
        )

    # Instaloader
    instaloader_cmd = resolve_instaloader()
    instaloader_ok = False
    instaloader_version = None
    instaloader_error = None
    instaloader_path = None

    if instaloader_cmd == "python-module":
        try:
            import instaloader as _il  # noqa: F401

            instaloader_ok = True
            instaloader_version = getattr(_il, "__version__", "installed")
            instaloader_path = f"{PYTHON_CMD} -m instaloader"
        except Exception as e:
            instaloader_error = str(e)[:200]
    elif instaloader_cmd:
        instaloader_ok = True
        instaloader_path = instaloader_cmd
        instaloader_version = "cli"
    else:
        instaloader_error = "not found — install with: pip install instaloader"

    # ExifTool
    exiftool_cmd = resolve_exiftool()
    exiftool_ok = bool(exiftool_cmd)
    exiftool_path = exiftool_cmd or "not found"
    exiftool_version = "installed" if exiftool_ok else None
    exiftool_error = None if exiftool_ok else "not found — install with apt-get install -y exiftool"

    return {
        "maigret": {
            "available": maigret_ok,
            "path": maigret_path or "not found",
            "version": maigret_version,
            "error": maigret_error,
        },
        "tookie": {
            "available": tookie_ok,
            "path": tookie_path or "not found",
            "version": tookie_version,
            "error": tookie_error,
            "note": "Tookie-OSINT V4 — High-performance username enumeration & webscraping",
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
        "ignorant": {
            "available": ignorant_ok,
            "path": ignorant_path or "not found",
            "version": ignorant_version,
            "error": ignorant_error,
            "note": "Phone registration check (IG/Snap/Amazon). Does not message target.",
        },
        "holehe": {
            "available": holehe_ok,
            "path": holehe_path or "not found",
            "version": holehe_version,
            "error": holehe_error,
            "note": "Email → registered accounts on 120+ sites (incl. Instagram). Does not notify target.",
        },
        "hibf": {
            "available": True,
            "path": f"{HIBF_BASE_URL}/api/search/text",
            "version": "frontend-api",
            "error": None,
            "note": (
                "License plate → public Flock LE search audit logs "
                "(Have I Been Flocked FOIA data — not a live camera hit)."
            ),
        },
        "toutatis": {
            "available": toutatis_ok,
            "package_installed": toutatis_pkg,
            "session_configured": session_configured,
            "path": toutatis_path or "not found",
            "version": toutatis_version,
            "error": toutatis_error,
            "note": (
                "Instagram username → public/obfuscated email & phone. "
                "Requires INSTAGRAM_SESSION_ID cookie."
            ),
        },
        "instaloader": {
            "available": instaloader_ok,
            "session_configured": session_configured,
            "path": instaloader_path or "not found",
            "version": instaloader_version,
            "error": instaloader_error,
            "note": (
                "Instagram username → bio, avatar, links. "
                "Uses INSTAGRAM_SESSION_ID when set (anonymous lookups are blocked)."
            ),
        },
        "exiftool": {
            "available": exiftool_ok,
            "path": exiftool_path,
            "version": exiftool_version,
            "error": exiftool_error,
            "note": "Image URL/file → EXIF metadata, camera fingerprinting & GPS reverse geocoding.",
        },
        "ready_for_scans": (
            maigret_ok
            or tookie_ok
            or sherlock_ok
            or blackbird_ok
            or spiderfoot_ok
            or ignorant_ok
            or holehe_ok
            or toutatis_ok
            or instaloader_ok
            or exiftool_ok
        ),
        "worker": True,
        "version": "2.5.0",
        "defaults": {
            "maigret_default": True,
            "tookie_default": True,
            "sherlock_default": True,
            "blackbird_default": False,
            "spiderfoot_default": False,
            "ignorant_default": False,
            "holehe_default": False,
            "toutatis_default": False,
            "blackbird_on_email": True,
            "holehe_on_email": True,
            "hibf_on_plate": True,
            "spiderfoot_on_phone": True,
            "ignorant_on_phone": True,
            "toutatis_on_username": True,
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


def parse_tookie_json(raw: Any) -> List[Dict]:
    """
    Parse Tookie-OSINT JSON output (dict or list of site findings).
    Tookie-OSINT returns findings across social & web services.
    """
    accounts: List[Dict] = []
    if not raw:
        return accounts

    VALID_STATUSES = {"claimed", "found", "taken", "active", "200", "ok", "exists", "true"}

    def _is_found(val: Any) -> bool:
        if not val:
            return False
        s = str(val).strip().lower()
        return s in VALID_STATUSES or any(w in s for w in ("claimed", "found", "taken"))

    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            st = item.get("status") or item.get("exists") or item.get("claim") or item.get("result")
            if not _is_found(st):
                continue
            platform = str(item.get("site") or item.get("site_name") or item.get("platform") or item.get("name") or "Unknown")
            url = item.get("url") or item.get("url_user") or item.get("link") or ""
            username = item.get("username") or item.get("user") or ""
            accounts.append({
                "platform": platform,
                "url": url,
                "username": username,
                "profile_data": item.get("data") or item.get("metadata") or {},
                "source": "tookie",
                "confidence": "found",
                "category": _categorize_platform(platform),
                "relevance": "unreviewed",
            })
        return accounts

    if isinstance(raw, dict):
        for site_name, site_data in raw.items():
            if site_name in ("summary", "stats", "metadata", "target"):
                continue
            if not isinstance(site_data, dict):
                continue
            st = site_data.get("status") or site_data.get("exists") or site_data.get("claim") or site_data.get("result")
            if not _is_found(st):
                continue
            url = site_data.get("url") or site_data.get("url_user") or site_data.get("link") or ""
            username = site_data.get("username") or site_data.get("user") or ""
            accounts.append({
                "platform": str(site_name),
                "url": url,
                "username": username,
                "profile_data": site_data,
                "source": "tookie",
                "confidence": "found",
                "category": _categorize_platform(str(site_name)),
                "relevance": "unreviewed",
            })
    return accounts


def parse_sherlock_json(raw: Any) -> List[Dict]:
    """
    Parse Sherlock JSON output (dict of site_name → {url_user, exists/status, ...}
    or list of site items).
    Supports standard sherlock-project output where status is in `exists`, `status`, or `claim`.
    """
    accounts: List[Dict] = []
    if not raw:
        return accounts

    VALID_STATUSES = {"claimed", "found", "taken", "active", "200", "ok"}

    def _is_claimed(val: Any) -> bool:
        if not val:
            return False
        s = str(val).strip().lower()
        return s in VALID_STATUSES or any(w in s for w in ("claimed", "found", "taken"))

    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            st = item.get("status") or item.get("exists") or item.get("claim")
            if not _is_claimed(st):
                continue
            site_name = str(item.get("site") or item.get("site_name") or item.get("platform") or "Unknown")
            url = item.get("url_user") or item.get("url") or item.get("url_main") or ""
            resp_time = item.get("response_time_s")
            profile_data = {"response_time_s": resp_time} if resp_time is not None else {}
            accounts.append({
                "platform": site_name,
                "url": url,
                "username": item.get("username", ""),
                "profile_data": profile_data,
                "source": "sherlock",
                "confidence": "found",
                "category": _categorize_platform(site_name),
                "relevance": "unreviewed",
            })
        return accounts

    if not isinstance(raw, dict):
        return accounts

    for site_name, site_data in raw.items():
        if not isinstance(site_data, dict):
            continue
        st = site_data.get("status") or site_data.get("exists") or site_data.get("claim")
        if not _is_claimed(st):
            continue
        url = site_data.get("url_user") or site_data.get("url") or site_data.get("url_main") or ""
        resp_time = site_data.get("response_time_s")
        profile_data = {"response_time_s": resp_time} if resp_time is not None else {}
        accounts.append({
            "platform": str(site_name),
            "url": url,
            "username": site_data.get("username", ""),
            "profile_data": profile_data,
            "source": "sherlock",
            "confidence": "found",
            "category": _categorize_platform(site_name),
            "relevance": "unreviewed",
        })
    return accounts


def parse_sherlock_csv(csv_path: str) -> List[Dict]:
    """Parse Sherlock CSV output file (username,name,url_user,status,response_time_s)."""
    accounts: List[Dict] = []
    if not os.path.isfile(csv_path):
        return accounts
    try:
        with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                st = str(row.get("exists") or row.get("status") or row.get("Status") or "").strip().lower()
                # Sherlock CSV "exists" is often Claimed/Available; some builds emit true/false
                if st in ("claimed", "found", "taken", "200", "ok", "true", "yes", "1"):
                    platform = str(row.get("name") or row.get("site") or row.get("platform") or "Unknown")
                    url = str(row.get("url_user") or row.get("url") or "")
                    user = str(row.get("username") or "")
                    resp_time = row.get("response_time_s")
                    profile_data = {"response_time_s": resp_time} if resp_time else {}
                    accounts.append({
                        "platform": platform,
                        "url": url,
                        "username": user,
                        "profile_data": profile_data,
                        "source": "sherlock",
                        "confidence": "found",
                        "category": _categorize_platform(platform),
                        "relevance": "unreviewed",
                    })
    except Exception as exc:
        log.warning("Failed to parse Sherlock CSV file %s: %s", csv_path, exc)
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


def parse_ignorant_results(
    raw: Any,
    *,
    country_code: str = "",
    national: str = "",
) -> Tuple[List[Dict], List[Dict]]:
    """
    Parse ignorant module output into (accounts, entities).

    Only sites with exists=True become accounts. Rate-limited sites are omitted
    from accounts but counted in profile_data of a summary entity when useful.
    """
    accounts: List[Dict] = []
    entities: List[Dict] = []
    if not raw:
        return accounts, entities

    results = raw if isinstance(raw, list) else raw.get("results") or []
    e164 = f"+{country_code}{national}" if country_code and national else ""
    rate_limited: List[str] = []
    checked: List[str] = []

    for item in results:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "unknown")
        domain = str(item.get("domain") or f"{name}.com")
        checked.append(name)
        if item.get("rateLimit"):
            rate_limited.append(name)
            continue
        if not item.get("exists"):
            continue
        platform = name.capitalize() if name.islower() else name
        accounts.append({
            "platform": platform,
            "url": f"https://{domain}",
            "username": "",
            "profile_data": {
                "phone_registered": True,
                "method": item.get("method") or "",
                "e164": e164,
                "country_code": country_code,
                "check": "ignorant_phone_registration",
            },
            "source": "ignorant",
            "confidence": "found",
            "category": _categorize_platform(platform),
            "relevance": "unreviewed",
        })

    if e164:
        entities.append({
            "type": "phone",
            "value": e164,
            "source": "ignorant",
            "module": "phone_check",
            "confidence": "high" if accounts else "medium",
            "context": (
                f"Checked {', '.join(checked) or 'modules'}"
                + (f"; rate-limited: {', '.join(rate_limited)}" if rate_limited else "")
            ),
            "relevance": "unreviewed",
        })
    return accounts, entities


def parse_holehe_results(raw: Any, *, email: str = "") -> Tuple[List[Dict], List[Dict]]:
    """
    Parse holehe module output into (accounts, entities).

    Only sites with exists=True become accounts. Recovery hints stay on
    profile_data for staff; callers must not log them.
    """
    accounts: List[Dict] = []
    entities: List[Dict] = []
    if not raw:
        return accounts, entities

    results = raw if isinstance(raw, list) else raw.get("results") or []
    rate_limited: List[str] = []
    checked: List[str] = []

    for item in results:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "unknown")
        domain = str(item.get("domain") or f"{name}.com")
        checked.append(name)
        if item.get("rateLimit"):
            rate_limited.append(name)
            continue
        if not item.get("exists"):
            continue
        platform = name.capitalize() if name.islower() else name
        pd: Dict[str, Any] = {
            "email_registered": True,
            "check": "holehe_email_registration",
        }
        rec = item.get("emailrecovery")
        phone_hint = item.get("phoneNumber")
        if rec:
            pd["email_recovery_hint"] = str(rec)
        if phone_hint:
            pd["phone_hint"] = str(phone_hint)
        others = item.get("others")
        if others:
            pd["others"] = others
        accounts.append({
            "platform": platform,
            "url": f"https://{domain}",
            "username": "",
            "profile_data": pd,
            "source": "holehe",
            "confidence": "found",
            "category": _categorize_platform(platform),
            "relevance": "unreviewed",
        })

    if email and "@" in email:
        local, _, domain = email.partition("@")
        redacted = f"{local[:1]}***@{domain}" if local else "***"
        entities.append({
            "type": "email",
            "value": redacted,
            "source": "holehe",
            "module": "email_check",
            "confidence": "high" if accounts else "medium",
            "context": (
                f"Checked {len(checked)} sites"
                + (f"; registered on {len(accounts)}" if accounts else "")
                + (f"; rate-limited: {', '.join(rate_limited[:8])}" if rate_limited else "")
            ),
            "relevance": "unreviewed",
        })
    return accounts, entities


def parse_toutatis_user(
    user: Dict[str, Any],
    *,
    lookup: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict], List[Dict]]:
    """
    Map Toutatis Instagram user dict + optional advanced_lookup into
    (accounts, entities). Redacts nothing in structured fields — callers
    must not log PII.
    """
    accounts: List[Dict] = []
    entities: List[Dict] = []
    if not user or not isinstance(user, dict):
        return accounts, entities

    username = str(user.get("username") or "").strip()
    if not username:
        return accounts, entities

    public_email = (user.get("public_email") or "").strip() or None
    public_phone = None
    if user.get("public_phone_number"):
        cc = str(user.get("public_phone_country_code") or "").strip()
        num = str(user.get("public_phone_number") or "").strip()
        public_phone = f"+{cc} {num}".strip() if cc else num

    obfuscated_email = None
    obfuscated_phone = None
    if isinstance(lookup, dict):
        obfuscated_email = (lookup.get("obfuscated_email") or "").strip() or None
        op = lookup.get("obfuscated_phone")
        if op is not None and str(op).strip():
            obfuscated_phone = str(op).strip()

    pic = ""
    try:
        pic = (user.get("hd_profile_pic_url_info") or {}).get("url") or ""
    except Exception:
        pic = ""

    profile_data = {
        "user_id": str(user.get("userID") or user.get("pk") or user.get("id") or ""),
        "full_name": user.get("full_name") or "",
        "biography": (user.get("biography") or "")[:500],
        "is_private": bool(user.get("is_private")),
        "is_verified": bool(user.get("is_verified")),
        "is_business": bool(user.get("is_business")),
        "follower_count": user.get("follower_count"),
        "following_count": user.get("following_count"),
        "media_count": user.get("media_count"),
        "external_url": user.get("external_url") or "",
        "public_email": public_email,
        "public_phone": public_phone,
        "obfuscated_email": obfuscated_email,
        "obfuscated_phone": obfuscated_phone,
        "profile_pic": pic,
        "is_whatsapp_linked": user.get("is_whatsapp_linked"),
    }

    accounts.append({
        "platform": "Instagram",
        "url": f"https://www.instagram.com/{username}/",
        "username": username,
        "profile_data": profile_data,
        "source": "toutatis",
        "confidence": "found",
        "category": "social",
        "relevance": "unreviewed",
    })

    if public_email:
        entities.append({
            "type": "email",
            "value": public_email,
            "source": "toutatis",
            "module": "public_email",
            "confidence": "high",
            "context": f"@{username} public_email",
            "relevance": "unreviewed",
        })
    if obfuscated_email:
        entities.append({
            "type": "email",
            "value": obfuscated_email,
            "source": "toutatis",
            "module": "obfuscated_email",
            "confidence": "medium",
            "context": f"@{username} obfuscated_email",
            "relevance": "unreviewed",
        })
    if public_phone:
        entities.append({
            "type": "phone",
            "value": public_phone,
            "source": "toutatis",
            "module": "public_phone",
            "confidence": "high",
            "context": f"@{username} public_phone",
            "relevance": "unreviewed",
        })
    if obfuscated_phone:
        entities.append({
            "type": "phone",
            "value": obfuscated_phone,
            "source": "toutatis",
            "module": "obfuscated_phone",
            "confidence": "medium",
            "context": f"@{username} obfuscated_phone",
            "relevance": "unreviewed",
        })
    if profile_data.get("full_name"):
        entities.append({
            "type": "name",
            "value": str(profile_data["full_name"])[:200],
            "source": "toutatis",
            "module": "full_name",
            "confidence": "medium",
            "context": f"@{username}",
            "relevance": "unreviewed",
        })

    return accounts, entities


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


async def run_tookie(
    username: Union[str, List[str]],
    deep: bool = False,
    tmpdir: str = "",
) -> Dict[str, Any]:
    """Run Tookie-OSINT for username(s). Performs high-performance username enumeration."""
    result_meta: Dict[str, Any] = {
        "tool": "tookie", "ok": False, "error": None,
        "warning": None, "raw": {}, "accounts": [],
    }

    if isinstance(username, str):
        target_users = [username.strip()] if username.strip() else []
    else:
        target_users = [u.strip() for u in (username or []) if u and len(u.strip()) >= 2]

    if not target_users:
        result_meta["error"] = "no valid usernames provided for tookie"
        return result_meta

    tookie_cmd = resolve_tookie_cmd()
    if not tookie_cmd:
        result_meta["error"] = "tookie-osint not installed"
        return result_meta

    out_dir = tmpdir or tempfile.mkdtemp(prefix="tookie_")

    if tookie_cmd.endswith(".py"):
        cmd = [PYTHON_CMD, tookie_cmd]
    elif tookie_cmd.startswith("python-module"):
        mod_name = tookie_cmd.split(":", 1)[1] if ":" in tookie_cmd else "tookie"
        cmd = [PYTHON_CMD, "-m", mod_name]
    else:
        cmd = [tookie_cmd]

    threads = "15" if deep else "10"
    u_arg = ",".join(target_users) if len(target_users) > 1 else target_users[0]

    cmd += ["-u", u_arg, "-o", "json", "-t", threads]
    if deep:
        cmd += ["-W"]

    log.info("Tookie-OSINT scan for %s deep=%s", [_redact(u) for u in target_users], deep)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=out_dir,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TOOKIE_TIMEOUT)
        stderr_s = (stderr or b"").decode("utf-8", errors="replace")
        stdout_s = (stdout or b"").decode("utf-8", errors="replace")

        all_accounts: List[Dict[str, Any]] = []
        raw_data: Dict[str, Any] = {}

        json_files = []
        if os.path.isdir(out_dir):
            for fn in os.listdir(out_dir):
                if fn.endswith(".json"):
                    json_files.append(os.path.join(out_dir, fn))

        if json_files:
            for jp in json_files:
                try:
                    with open(jp, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    base_fn = os.path.basename(jp).removesuffix(".json")
                    raw_data[base_fn] = data
                    parsed = parse_tookie_json(data)
                    for acct in parsed:
                        if not acct.get("username") and len(target_users) == 1:
                            acct["username"] = target_users[0]
                    all_accounts.extend(parsed)
                except Exception as exc:
                    log.warning("Failed to parse Tookie JSON file %s: %s", jp, exc)

        if not json_files and stdout_s.strip():
            try:
                if stdout_s.strip().startswith(("{", "[")):
                    raw_obj = json.loads(stdout_s)
                    raw_data["stdout"] = raw_obj
                    parsed = parse_tookie_json(raw_obj)
                    all_accounts.extend(parsed)
            except Exception:
                pass

        accounts = dedupe_accounts(all_accounts)
        result_meta.update({"ok": True, "raw": raw_data, "accounts": accounts})
        if not accounts:
            result_meta["warning"] = f"tookie scanned {len(target_users)} username(s) but found 0 accounts"
        return result_meta

    except asyncio.TimeoutError:
        result_meta["error"] = f"tookie timed out after {TOOKIE_TIMEOUT}s"
    except FileNotFoundError:
        result_meta["error"] = f"tookie executable not found ({tookie_cmd})"
    except Exception as exc:
        result_meta["error"] = f"tookie error: {exc}"
    return result_meta


async def run_sherlock(
    username: Union[str, List[str]],
    deep: bool = False,
    tmpdir: str = "",
) -> Dict[str, Any]:
    """Run Sherlock for one or multiple usernames."""
    result_meta: Dict[str, Any] = {
        "tool": "sherlock", "ok": False, "error": None,
        "warning": None, "raw": {}, "accounts": [],
    }

    if isinstance(username, str):
        target_users = [username.strip()] if username.strip() else []
    else:
        target_users = [u.strip() for u in (username or []) if u and len(u.strip()) >= 2]

    if not target_users:
        result_meta["error"] = "no valid usernames provided"
        return result_meta

    sherlock_cmd = resolve_sherlock_cmd()
    if not sherlock_cmd:
        result_meta["error"] = "sherlock not installed"
        return result_meta

    out_dir = tmpdir or tempfile.mkdtemp(prefix="sherlock_")

    if sherlock_cmd.startswith("python-module"):
        mod_name = sherlock_cmd.split(":", 1)[1] if ":" in sherlock_cmd else "sherlock_project"
        cmd = [PYTHON_CMD, "-m", mod_name]
    else:
        cmd = [sherlock_cmd]

    timeout_s = "15" if deep else "10"
    cmd += [
        "--folderoutput", out_dir,
        "--csv",
        "--no-color",
        "--timeout", timeout_s,
    ]
    cmd += target_users

    log.info("Sherlock scan for %s deep=%s", [_redact(u) for u in target_users], deep)

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

        csv_files = []
        json_files = []
        if os.path.isdir(out_dir):
            for fn in os.listdir(out_dir):
                fp = os.path.join(out_dir, fn)
                if fn.endswith(".csv"):
                    csv_files.append(fp)
                elif fn.endswith(".json"):
                    json_files.append(fp)

        raw_outputs: Dict[str, Any] = {}
        all_accounts: List[Dict[str, Any]] = []

        if csv_files:
            for cp in csv_files:
                parsed = parse_sherlock_csv(cp)
                base_fn = os.path.basename(cp).removesuffix(".csv")
                raw_outputs[base_fn] = {"csv_file": cp, "found_count": len(parsed)}
                for acct in parsed:
                    if not acct.get("username") and len(target_users) == 1:
                        acct["username"] = target_users[0]
                all_accounts.extend(parsed)

        if json_files:
            for jp in json_files:
                try:
                    with open(jp, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    base_fn = os.path.basename(jp).removesuffix(".json")
                    raw_outputs[base_fn] = data
                    parsed = parse_sherlock_json(data)
                    for acct in parsed:
                        if not acct.get("username") and len(target_users) == 1:
                            acct["username"] = target_users[0]
                    all_accounts.extend(parsed)
                except Exception as exc:
                    log.warning("Failed to parse Sherlock JSON %s: %s", jp, exc)

        if not csv_files and not json_files:
            try:
                raw = json.loads(stdout_s) if stdout_s.strip().startswith(("{", "[")) else {}
            except Exception:
                raw = {}
            if raw:
                raw_outputs["stdout"] = raw
                all_accounts = parse_sherlock_json(raw)

        if not raw_outputs and proc.returncode != 0 and not all_accounts:
            result_meta["error"] = f"sherlock failed (exit {proc.returncode}): {stderr_s[:200] or stdout_s[:200]}"
            return result_meta

        accounts = dedupe_accounts(all_accounts)
        result_meta.update({"ok": True, "raw": raw_outputs, "accounts": accounts})
        if not accounts:
            result_meta["warning"] = f"sherlock scanned {len(target_users)} username(s) but found 0 accounts"
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


async def run_toutatis(
    usernames: List[str],
    *,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run Toutatis Instagram enrichment for one or more usernames.

    Requires a valid Instagram ``sessionid`` cookie
    (``INSTAGRAM_SESSION_ID`` / ``TOUTATIS_SESSION_ID``).
    Never logs the session cookie or recovered PII at INFO level.
    """
    result_meta: Dict[str, Any] = {
        "tool": "toutatis",
        "ok": False,
        "error": None,
        "warning": None,
        "raw": {},
        "accounts": [],
        "entities": [],
    }

    users = [
        re.sub(r"^@", "", (u or "").strip())
        for u in (usernames or [])
        if u and len((u or "").strip()) >= 2
    ]
    users = users[:MAX_TOUTATIS_USERNAMES]
    if not users:
        result_meta["error"] = "no usernames provided for toutatis"
        return result_meta

    if not resolve_toutatis():
        result_meta["error"] = "toutatis not installed"
        return result_meta

    sid = (session_id or get_instagram_session_id()).strip()
    if not sid:
        result_meta["error"] = (
            "INSTAGRAM_SESSION_ID not set — add Instagram browser cookie "
            "'sessionid' to osint-worker env"
        )
        return result_meta

    log.info(
        "Toutatis scan usernames=%s session=%s",
        [_redact(u) for u in users],
        _redact(sid),
    )

    def _lookup_one(username: str) -> Dict[str, Any]:
        from toutatis.core import advanced_lookup, getInfo

        info = getInfo(username, sid, searchType="username")
        if info.get("error") or not info.get("user"):
            return {
                "username": username,
                "error": info.get("error") or "not found",
                "user": None,
                "lookup": None,
            }
        user = info["user"]
        lookup_raw = advanced_lookup(user.get("username") or username)
        lookup_user = None
        if not lookup_raw.get("error") and isinstance(lookup_raw.get("user"), dict):
            lu = lookup_raw["user"]
            # Ignore "No users found" / rate messages without fields
            if "obfuscated_email" in lu or "obfuscated_phone" in lu:
                lookup_user = lu
            elif lu.get("message") and "No users" not in str(lu.get("message")):
                lookup_user = {"message": lu.get("message")}
        return {
            "username": username,
            "error": None,
            "user": user,
            "lookup": lookup_user,
            "lookup_error": lookup_raw.get("error"),
        }

    try:
        all_accounts: List[Dict] = []
        all_entities: List[Dict] = []
        raw_by_user: Dict[str, Any] = {}
        errors: List[str] = []
        warnings: List[str] = []

        async def _run_all() -> List[Dict[str, Any]]:
            tasks = [
                asyncio.to_thread(_lookup_one, u) for u in users
            ]
            return list(await asyncio.gather(*tasks, return_exceptions=True))

        results = await asyncio.wait_for(
            _run_all(), timeout=float(TOUTATIS_TIMEOUT)
        )

        for u, res in zip(users, results):
            if isinstance(res, Exception):
                errors.append(f"{u}: {res}")
                raw_by_user[u] = {"error": str(res)}
                continue
            if not isinstance(res, dict):
                errors.append(f"{u}: bad result")
                continue
            if res.get("error") or not res.get("user"):
                errors.append(f"{u}: {res.get('error') or 'not found'}")
                raw_by_user[u] = {"error": res.get("error")}
                continue
            if res.get("lookup_error") == "rate limit":
                warnings.append(f"{u}: advanced_lookup rate-limited")

            # Sanitize raw for storage — drop huge nested blobs, keep key fields
            user = res["user"]
            safe_raw = {
                "username": user.get("username"),
                "userID": user.get("userID") or user.get("pk"),
                "full_name": user.get("full_name"),
                "is_private": user.get("is_private"),
                "is_verified": user.get("is_verified"),
                "follower_count": user.get("follower_count"),
                "following_count": user.get("following_count"),
                "media_count": user.get("media_count"),
                "has_public_email": bool(user.get("public_email")),
                "has_public_phone": bool(user.get("public_phone_number")),
                "has_obfuscated_email": bool(
                    (res.get("lookup") or {}).get("obfuscated_email")
                ),
                "has_obfuscated_phone": bool(
                    (res.get("lookup") or {}).get("obfuscated_phone")
                ),
            }
            raw_by_user[u] = safe_raw

            accts, ents = parse_toutatis_user(
                user, lookup=res.get("lookup")
            )
            all_accounts.extend(accts)
            all_entities.extend(ents)

        if not all_accounts and errors:
            result_meta["error"] = "; ".join(errors)[:400]
            result_meta["raw"] = {"profiles": raw_by_user}
            return result_meta

        result_meta.update({
            "ok": True,
            "raw": {"profiles": raw_by_user},
            "accounts": all_accounts,
            "entities": all_entities,
        })
        if errors:
            warnings.append(f"partial failures: {'; '.join(errors)[:200]}")
        if warnings:
            result_meta["warning"] = "; ".join(warnings)
        return result_meta

    except asyncio.TimeoutError:
        result_meta["error"] = f"toutatis timed out after {TOUTATIS_TIMEOUT}s"
    except Exception as exc:
        result_meta["error"] = f"toutatis error: {exc}"
    return result_meta


async def run_instaloader(usernames: List[str]) -> Dict[str, Any]:
    """
    Run Instaloader profile metadata & HD avatar extraction for one or more Instagram usernames.
    """
    result_meta: Dict[str, Any] = {
        "tool": "instaloader",
        "ok": False,
        "error": None,
        "warning": None,
        "raw": {},
        "accounts": [],
        "entities": [],
    }
    users = [re.sub(r"^@", "", (u or "").strip()) for u in (usernames or []) if u and len((u or "").strip()) >= 2]
    if not users:
        result_meta["error"] = "no usernames provided for instaloader"
        return result_meta

    if not resolve_instaloader():
        result_meta["error"] = "instaloader not installed"
        return result_meta

    def _scrape_one(u: str) -> Dict[str, Any]:
        try:
            import instaloader
            L = instaloader.Instaloader(
                download_pictures=False,
                download_videos=False,
                download_comments=False,
                save_metadata=False,
                quiet=True,
            )
            sid = get_instagram_session_id()
            if sid:
                L.context._session.cookies.set(
                    "sessionid", sid, domain=".instagram.com", path="/"
                )
                ds_user = sid.split(":", 1)[0]
                if ds_user.isdigit():
                    L.context._session.cookies.set(
                        "ds_user_id", ds_user, domain=".instagram.com", path="/"
                    )
            profile = instaloader.Profile.from_username(L.context, u)
            return {
                "username": profile.username,
                "full_name": profile.full_name,
                "biography": profile.biography,
                "external_url": profile.external_url,
                "profile_pic_url": profile.profile_pic_url,
                "followers": profile.followers,
                "followees": profile.followees,
                "mediacount": profile.mediacount,
                "is_private": profile.is_private,
                "is_verified": profile.is_verified,
                "business_category_name": profile.business_category_name,
                "error": None,
            }
        except Exception as exc:
            return {"username": u, "error": str(exc)}

    try:
        results = await asyncio.gather(*[asyncio.to_thread(_scrape_one, u) for u in users], return_exceptions=True)
        all_accounts = []
        all_entities = []
        raw_by_user = {}
        errors = []

        for u, res in zip(users, results):
            if isinstance(res, Exception) or not isinstance(res, dict) or res.get("error"):
                err_msg = str(res.get("error") if isinstance(res, dict) else res)
                errors.append(f"{u}: {err_msg}")
                raw_by_user[u] = {"error": err_msg}
                continue

            raw_by_user[u] = res
            account_url = f"https://www.instagram.com/{res['username']}/"
            all_accounts.append({
                "site": "Instagram",
                "username": res["username"],
                "url": account_url,
                "source": "instaloader",
                "category": "social",
                "details": f"Followers: {res['followers']} | Following: {res['followees']} | Posts: {res['mediacount']} | Private: {res['is_private']}",
            })
            if res.get("full_name"):
                all_entities.append({"type": "full_name", "value": res["full_name"], "source": "instaloader"})
            if res.get("external_url"):
                all_entities.append({"type": "website", "value": res["external_url"], "source": "instaloader"})
            if res.get("biography"):
                all_entities.append({"type": "bio", "value": res["biography"], "source": "instaloader"})

        if not all_accounts and errors:
            result_meta["error"] = "; ".join(errors)[:400]
            return result_meta

        result_meta.update({
            "ok": True,
            "raw": {"profiles": raw_by_user},
            "accounts": all_accounts,
            "entities": all_entities,
        })
        return result_meta

    except Exception as exc:
        result_meta["error"] = f"instaloader error: {exc}"
        return result_meta


async def run_exiftool(image_url_or_path: str) -> Dict[str, Any]:
    """
    Run ExifTool image metadata & GPS geolocation extraction.
    Automatically resolves GPS coordinates to a physical address via OpenStreetMap Nominatim.
    """
    result_meta: Dict[str, Any] = {
        "tool": "exiftool",
        "ok": False,
        "error": None,
        "warning": None,
        "raw": {},
        "metadata": {},
        "gps": None,
        "address": None,
    }

    exif_cmd = resolve_exiftool()
    if not exif_cmd:
        result_meta["error"] = "exiftool binary not installed"
        return result_meta

    if not image_url_or_path:
        result_meta["error"] = "no image URL or file path provided"
        return result_meta

    tmp_path = None
    target_file = image_url_or_path
    if image_url_or_path.startswith(("http://", "https://")):
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(image_url_or_path)
                resp.raise_for_status()
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                tmp.write(resp.content)
                tmp.close()
                tmp_path = tmp.name
                target_file = tmp_path
        except Exception as exc:
            result_meta["error"] = f"failed to download image: {exc}"
            return result_meta

    try:
        proc = await asyncio.create_subprocess_exec(
            exif_cmd, "-j", "-n", target_file,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        stdout_s = (stdout or b"").decode("utf-8", errors="replace")

        if not stdout_s.strip():
            result_meta["error"] = "exiftool returned empty output"
            return result_meta

        parsed = json.loads(stdout_s)
        meta = parsed[0] if isinstance(parsed, list) and len(parsed) > 0 else {}

        gps_data = None
        address = None
        if "GPSLatitude" in meta and "GPSLongitude" in meta:
            lat = meta["GPSLatitude"]
            lon = meta["GPSLongitude"]
            alt = meta.get("GPSAltitude")
            gps_data = {"latitude": lat, "longitude": lon, "altitude": alt}

            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    geo_resp = await client.get(
                        "https://nominatim.openstreetmap.org/reverse",
                        params={"lat": lat, "lon": lon, "format": "json"},
                        headers={"User-Agent": "ShamrockLeads-OSINT/1.0"},
                    )
                    if geo_resp.status_code == 200:
                        address = geo_resp.json().get("display_name")
            except Exception:
                pass

        result_meta.update({
            "ok": True,
            "raw": meta,
            "metadata": {
                "make": meta.get("Make"),
                "model": meta.get("Model"),
                "create_date": meta.get("CreateDate") or meta.get("DateTimeOriginal"),
                "software": meta.get("Software"),
                "image_width": meta.get("ImageWidth"),
                "image_height": meta.get("ImageHeight"),
            },
            "gps": gps_data,
            "address": address,
        })
        return result_meta

    except Exception as exc:
        result_meta["error"] = f"exiftool execution failed: {exc}"
        return result_meta
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


async def run_ignorant(phone: str) -> Dict[str, Any]:
    """
    Run Ignorant phone-registration checks (Instagram, Snapchat, Amazon).

    Does **not** send SMS or otherwise notify the target number.
    Uses the public async module API (httpx) rather than the CLI.
    """
    result_meta: Dict[str, Any] = {
        "tool": "ignorant",
        "ok": False,
        "error": None,
        "warning": None,
        "raw": {},
        "accounts": [],
        "entities": [],
    }
    parsed = parse_phone_for_ignorant(phone)
    if not parsed:
        result_meta["error"] = "invalid or empty phone number"
        return result_meta
    if not resolve_ignorant():
        result_meta["error"] = "ignorant not installed"
        return result_meta

    country_code, national = parsed
    log.info(
        "Ignorant phone check e164=+%s…%s",
        country_code,
        national[-4:] if len(national) >= 4 else "****",
    )

    try:
        import httpx
        from ignorant.core import get_functions, import_submodules, launch_module
    except ImportError as exc:
        result_meta["error"] = f"ignorant import failed: {exc}"
        return result_meta

    try:
        modules = import_submodules("ignorant.modules")
        websites = get_functions(modules)
        if not websites:
            result_meta["error"] = "ignorant: no modules discovered"
            return result_meta

        out: List[Dict[str, Any]] = []
        timeout = httpx.Timeout(float(min(IGNORANT_TIMEOUT, 30)))

        async def _run() -> None:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                await asyncio.gather(
                    *[
                        launch_module(mod, national, country_code, client, out)
                        for mod in websites
                    ]
                )

        await asyncio.wait_for(_run(), timeout=float(IGNORANT_TIMEOUT))

        accounts, entities = parse_ignorant_results(
            out, country_code=country_code, national=national
        )
        # Ignore snapchat rateLimit flag because Snapchat deprecated the legacy xsrf endpoint (false alarm)
        rate_limited = [
            r for r in out
            if isinstance(r, dict) and r.get("rateLimit") and r.get("name") != "snapchat"
        ]
        result_meta.update({
            "ok": True,
            "raw": {
                "results": out,
                "country_code": country_code,
                "national_redacted": f"***{national[-4:]}" if len(national) >= 4 else "****",
            },
            "accounts": accounts,
            "entities": entities,
        })
        if rate_limited and not accounts:
            result_meta["warning"] = (
                f"ignorant rate-limited on: "
                f"{', '.join(str(r.get('name')) for r in rate_limited)}"
            )
        elif rate_limited:
            result_meta["warning"] = (
                f"partial rate-limit on: "
                f"{', '.join(str(r.get('name')) for r in rate_limited)}"
            )
        return result_meta
    except asyncio.TimeoutError:
        result_meta["error"] = f"ignorant timed out after {IGNORANT_TIMEOUT}s"
    except Exception as exc:
        result_meta["error"] = f"ignorant error: {exc}"
    return result_meta


def _valid_email(email: str) -> bool:
    e = (email or "").strip()
    if "@" not in e or " " in e:
        return False
    local, _, domain = e.partition("@")
    return bool(local) and "." in domain and len(e) >= 6


async def run_holehe(email: str) -> Dict[str, Any]:
    """
    Run Holehe email-registration checks (120+ sites including Instagram).

    Does **not** send mail or otherwise notify the target address.
    Same megadose module API as Ignorant (httpx + launch_module).
    """
    result_meta: Dict[str, Any] = {
        "tool": "holehe",
        "ok": False,
        "error": None,
        "warning": None,
        "raw": {},
        "accounts": [],
        "entities": [],
    }
    addr = (email or "").strip()
    if not _valid_email(addr):
        result_meta["error"] = "invalid or empty email"
        return result_meta
    if not resolve_holehe():
        result_meta["error"] = "holehe not installed"
        return result_meta

    local, _, domain = addr.partition("@")
    log.info("Holehe email check local=%s domain=%s", _redact(local), domain)

    try:
        import httpx
        from holehe.core import get_functions, import_submodules, launch_module
    except ImportError as exc:
        result_meta["error"] = f"holehe import failed: {exc}"
        return result_meta

    try:
        modules = import_submodules("holehe.modules")
        websites = get_functions(modules)
        if not websites:
            result_meta["error"] = "holehe: no modules discovered"
            return result_meta

        out: List[Dict[str, Any]] = []
        timeout = httpx.Timeout(float(min(HOLEHE_TIMEOUT, 25)))

        async def _run() -> None:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                await asyncio.gather(
                    *[launch_module(mod, addr, client, out) for mod in websites]
                )

        await asyncio.wait_for(_run(), timeout=float(HOLEHE_TIMEOUT))

        accounts, entities = parse_holehe_results(out, email=addr)
        rate_limited = [
            r for r in out
            if isinstance(r, dict) and r.get("rateLimit")
        ]
        # Do not persist full email or recovery hints in raw
        safe_hits = []
        for r in out:
            if not isinstance(r, dict):
                continue
            if not r.get("exists") and not r.get("rateLimit"):
                continue
            safe_hits.append({
                "name": r.get("name"),
                "exists": bool(r.get("exists")),
                "rateLimit": bool(r.get("rateLimit")),
                "has_emailrecovery": bool(r.get("emailrecovery")),
                "has_phoneNumber": bool(r.get("phoneNumber")),
            })
        result_meta.update({
            "ok": True,
            "raw": {
                "hits": safe_hits,
                "checked": len(out),
                "domain": domain,
            },
            "accounts": accounts,
            "entities": entities,
        })
        if rate_limited and not accounts:
            result_meta["warning"] = (
                f"holehe rate-limited on: "
                f"{', '.join(str(r.get('name')) for r in rate_limited[:10])}"
            )
        elif rate_limited:
            result_meta["warning"] = (
                f"partial rate-limit on {len(rate_limited)} sites"
            )
        return result_meta
    except asyncio.TimeoutError:
        result_meta["error"] = f"holehe timed out after {HOLEHE_TIMEOUT}s"
    except Exception as exc:
        result_meta["error"] = f"holehe error: {exc}"
    return result_meta


def normalize_plate(plate: str) -> Optional[str]:
    raw = (plate or "").strip().upper().replace(" ", "")
    if not raw or not re.fullmatch(r"[A-Z0-9-]{2,10}", raw):
        return None
    return raw


def hibf_plate_hash(plate: str) -> str:
    """SHA-256 prefix used by haveibeenflocked.com (lowercase trim, first 8 hex)."""
    return hashlib.sha256(plate.lower().strip().encode("utf-8")).hexdigest()[:8]


def hibf_plate_hashes(plate: str, *, max_variants: int = 10) -> List[str]:
    """Exact plate hash plus O/0 and I/1 lookalikes (same as the HIBF site)."""
    base = normalize_plate(plate)
    if not base:
        return []
    variants = {base}
    slots: List[Tuple[int, Tuple[str, ...]]] = []
    for i, ch in enumerate(base):
        if ch in ("O", "0"):
            slots.append((i, ("O", "0")))
        elif ch in ("I", "1"):
            slots.append((i, ("I", "1")))

    if slots:
        chars = list(base)

        def _walk(idx: int) -> None:
            if len(variants) >= max_variants:
                return
            if idx == len(slots):
                variants.add("".join(chars))
                return
            pos, opts = slots[idx]
            for opt in opts:
                chars[pos] = opt
                _walk(idx + 1)

        _walk(0)

    hashes: List[str] = []
    seen: set[str] = set()
    for variant in list(variants)[:max_variants]:
        digest = hibf_plate_hash(variant)
        if digest not in seen:
            seen.add(digest)
            hashes.append(digest)
    return hashes


def parse_hibf_results(raw: Any) -> Tuple[List[Dict], List[Dict]]:
    """Map HIBF /api/search/text JSON into (accounts, entities). Never stores the plate."""
    accounts: List[Dict] = []
    entities: List[Dict] = []
    if not isinstance(raw, dict):
        return accounts, entities
    rows = raw.get("results") or []
    for item in rows:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or "").strip()
        agency = str(item.get("org_name") or item.get("agency_name") or item.get("org_id") or "Flock agency")
        search_type = str(item.get("search_type") or "")
        when = str(item.get("search_time_utc") or item.get("start_timeframe_utc") or "")
        pd = {
            "le_searched": True,
            "check": "hibf_flock_audit",
            "reason": reason[:240] if reason and reason.upper() not in {"REDACTED", "***"} else None,
            "search_type": search_type or None,
            "search_time_utc": when or None,
            "devices_searched": item.get("total_devices_searched"),
            "networks_searched": item.get("total_networks_searched"),
            "plate_hash": str(item.get("license_plate_hash") or "")[:8] or None,
        }
        pd = {k: v for k, v in pd.items() if v not in (None, "")}
        accounts.append({
            "platform": f"Flock audit · {agency}",
            "url": f"{HIBF_BASE_URL}/",
            "username": "",
            "profile_data": pd,
            "source": "hibf",
            "confidence": "found",
            "category": "other",
            "relevance": "unreviewed",
        })
    if accounts:
        entities.append({
            "type": "other",
            "value": f"{len(accounts)} Flock LE search record(s)",
            "source": "hibf",
            "module": "plate_audit",
            "confidence": "medium",
            "context": "Public FOIA audit logs — not a live camera sighting.",
            "relevance": "unreviewed",
        })
    return accounts, entities


async def run_hibf(license_plate: str) -> Dict[str, Any]:
    """
    Look up public Flock LE search audit logs via Have I Been Flocked.

    Sends only the site's 8-char SHA-256 plate prefix — never the raw plate.
    Data is incomplete FOIA/transparency-portal logs, not live ALPR hits.
    """
    result_meta: Dict[str, Any] = {
        "tool": "hibf",
        "ok": False,
        "error": None,
        "warning": None,
        "raw": {},
        "accounts": [],
        "entities": [],
    }
    plate = normalize_plate(license_plate)
    if not plate:
        result_meta["error"] = "invalid or empty license plate"
        return result_meta

    hashes = hibf_plate_hashes(plate)
    log.info("HIBF plate check hashes=%d prefix=%s", len(hashes), hashes[0] if hashes else "none")

    url = f"{HIBF_BASE_URL}/api/search/text"
    try:
        async with httpx.AsyncClient(timeout=float(HIBF_TIMEOUT), follow_redirects=True) as client:
            r = await client.post(
                url,
                json={"plates": hashes, "cursor": None},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "ShamrockLeads-osint-worker/1.0",
                    "Origin": HIBF_BASE_URL,
                    "Referer": f"{HIBF_BASE_URL}/",
                },
            )
        if r.status_code == 429:
            result_meta["error"] = "hibf rate-limited — retry later"
            return result_meta
        if r.status_code >= 400:
            result_meta["error"] = f"hibf HTTP {r.status_code}"
            return result_meta
        payload = r.json() if r.content else {}
        accounts, entities = parse_hibf_results(payload)
        result_meta.update({
            "ok": True,
            "raw": {
                "count": payload.get("count") if isinstance(payload, dict) else 0,
                "total": payload.get("total") if isinstance(payload, dict) else 0,
                "hash_count": len(hashes),
            },
            "accounts": accounts,
            "entities": entities,
        })
        if not accounts:
            result_meta["warning"] = (
                "No public Flock audit-log hits (dataset is incomplete FOIA data)."
            )
        return result_meta
    except httpx.TimeoutException:
        result_meta["error"] = f"hibf timed out after {HIBF_TIMEOUT}s"
    except Exception as exc:
        result_meta["error"] = f"hibf error: {exc}"
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
    license_plate: Optional[str] = None,
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
            elif engine == "tookie" and mg_users:
                tasks.append(run_tookie(mg_users, deep=deep_scan, tmpdir=tmpdir))
                task_map.append(engine)
            elif engine == "sherlock" and mg_users:
                tasks.append(run_sherlock(mg_users, deep=deep_scan, tmpdir=tmpdir))
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
            elif engine == "ignorant":
                if phone and str(phone).strip():
                    tasks.append(run_ignorant(str(phone).strip()))
                    task_map.append(engine)
                else:
                    progress[engine]["status"] = "skipped"
                    progress[engine]["error"] = "No phone number for ignorant"
            elif engine == "holehe":
                if email and str(email).strip():
                    tasks.append(run_holehe(str(email).strip()))
                    task_map.append(engine)
                else:
                    progress[engine]["status"] = "skipped"
                    progress[engine]["error"] = "No email for holehe"
            elif engine == "hibf":
                if license_plate and str(license_plate).strip():
                    tasks.append(run_hibf(str(license_plate).strip()))
                    task_map.append(engine)
                else:
                    progress[engine]["status"] = "skipped"
                    progress[engine]["error"] = "No license plate for hibf"
            elif engine == "toutatis":
                if mg_users:
                    tasks.append(run_toutatis(mg_users))
                    task_map.append(engine)
                else:
                    progress[engine]["status"] = "skipped"
                    progress[engine]["error"] = "No usernames for toutatis"
            elif engine == "instaloader":
                if mg_users:
                    tasks.append(run_instaloader(mg_users))
                    task_map.append(engine)
                else:
                    progress[engine]["status"] = "skipped"
                    progress[engine]["error"] = "No usernames for instaloader"
            elif engine == "exiftool":
                target_img = email if (email and email.startswith(("http://", "https://"))) else None
                if target_img:
                    tasks.append(run_exiftool(target_img))
                    task_map.append(engine)
                else:
                    progress[engine]["status"] = "skipped"
                    progress[engine]["error"] = "No image URL provided for exiftool (pass image URL in context/email field)"
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
