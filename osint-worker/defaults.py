"""
OSINT scan policy defaults — ShamrockLeads osint-worker v2
===========================================================
Noise control and underwriting-friendly defaults.
Engines: Maigret · Sherlock · Blackbird · SpiderFoot
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

# ── Timeouts ──────────────────────────────────────────────────────────────────
MAIGRET_TIMEOUT = int(os.getenv("OSINT_MAIGRET_TIMEOUT", "180"))
SHERLOCK_TIMEOUT = int(os.getenv("OSINT_SHERLOCK_TIMEOUT", "120"))
BLACKBIRD_TIMEOUT = int(os.getenv("OSINT_BLACKBIRD_TIMEOUT", "150"))
SPIDERFOOT_TIMEOUT = int(os.getenv("OSINT_SPIDERFOOT_TIMEOUT", "300"))
MAIGRET_SITE_TIMEOUT = int(os.getenv("OSINT_MAIGRET_SITE_TIMEOUT", "12"))

# ── Quick vs deep site coverage ───────────────────────────────────────────────
QUICK_TOP_SITES = int(os.getenv("OSINT_QUICK_TOP_SITES", "250"))
DEEP_TOP_SITES = int(os.getenv("OSINT_DEEP_TOP_SITES", "800"))
MAX_NAME_DERIVED_USERNAMES = int(os.getenv("OSINT_MAX_NAME_USERNAMES", "2"))
MAX_MAIGRET_USERNAMES = int(os.getenv("OSINT_MAX_MAIGRET_USERNAMES", "2"))

# Always disable Maigret recursion / ID permutation noise
MAIGRET_NO_RECURSION = True
MAIGRET_NO_AUTOUPDATE = True

# Degraded quality thresholds
DEGRADED_ACCESS_DENIED_RATIO = float(os.getenv("OSINT_DEGRADED_ACCESS_RATIO", "0.15"))
DEGRADED_BOT_RATIO = float(os.getenv("OSINT_DEGRADED_BOT_RATIO", "0.10"))


def resolve_tool_flags(
    *,
    email: Optional[str],
    run_maigret: Optional[bool],
    run_blackbird: Optional[bool],
    second_opinion: bool = False,
) -> Tuple[bool, bool, List[str]]:
    """
    Legacy v1 policy resolver.
    Policy:
      - Maigret ON by default.
      - Blackbird OFF by default.
      - Blackbird ON when email is provided.
      - Blackbird ON when second_opinion=True.
    """
    notes: List[str] = []

    if run_maigret is None:
        want_maigret = True
        notes.append("policy: maigret default ON")
    else:
        want_maigret = bool(run_maigret)
        notes.append(f"policy: maigret explicit={'on' if want_maigret else 'off'}")

    if run_blackbird is None:
        if email and str(email).strip():
            want_blackbird = True
            notes.append("policy: blackbird ON (email-focused)")
        elif second_opinion:
            want_blackbird = True
            notes.append("policy: blackbird ON (second opinion)")
        else:
            want_blackbird = False
            notes.append("policy: blackbird default OFF")
    else:
        want_blackbird = bool(run_blackbird)
        notes.append(f"policy: blackbird explicit={'on' if want_blackbird else 'off'}")

    if second_opinion and want_blackbird and want_maigret:
        notes.append("policy: dual-engine second-opinion scan")

    return want_maigret, want_blackbird, notes


def build_username_candidates(
    usernames: Optional[List[str]],
    full_name: Optional[str],
) -> List[str]:
    """
    Prefer explicit usernames. Cap low-quality name-derived guesses.
    """
    ordered: List[str] = []
    seen: set[str] = set()

    def _add(u: str) -> None:
        key = (u or "").strip()
        if len(key) < 3:
            return
        low = key.lower()
        if low in seen:
            return
        if key.isdigit():
            return
        seen.add(low)
        ordered.append(key)

    for u in usernames or []:
        _add(u)

    derived: List[str] = []
    if full_name:
        parts = full_name.lower().split()
        if len(parts) >= 2:
            first, last = parts[0], parts[-1]
            candidates = [
                f"{first}.{last}",
                f"{first}{last}",
                f"{first}_{last}",
                f"{first[0]}{last}",
            ]
            for c in candidates:
                if len(derived) >= MAX_NAME_DERIVED_USERNAMES:
                    break
                low = c.lower()
                if low in seen:
                    continue
                derived.append(c)
                seen.add(low)

    ordered.extend(derived)
    return ordered[:max(MAX_MAIGRET_USERNAMES + 2, 4)]


def maigret_site_args(deep_scan: bool) -> List[str]:
    """CLI args for site coverage."""
    if deep_scan:
        return ["--top-sites", str(DEEP_TOP_SITES)]
    return ["--top-sites", str(QUICK_TOP_SITES)]


def dedupe_accounts(accounts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Dedupe by (source, platform lower, url host+path)."""
    from urllib.parse import urlparse

    out: List[Dict[str, Any]] = []
    seen: set = set()
    for a in accounts:
        if not isinstance(a, dict):
            continue
        url = str(a.get("url") or "")
        host = ""
        path = ""
        try:
            p = urlparse(url)
            host = (p.netloc or "").lower().removeprefix("www.")
            path = (p.path or "").rstrip("/").lower()
        except Exception:
            host = url.lower()
        platform = str(a.get("platform") or "").lower().strip()
        source = str(a.get("source") or "")
        key = (source, platform, host, path) if host else (source, platform, url)
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


def score_signals(accounts: List[Dict], subject_type: str = "defendant") -> Tuple[int, List[Dict]]:
    """0-40 OSINT risk delta + signals (advisory only)."""
    import json as _json

    signals: List[Dict] = []
    score = 0
    total = len(accounts)

    if total >= 30:
        score += 20
        signals.append({
            "signal_type": "high_account_count",
            "severity": "high",
            "detail": f"{total} accounts found — possible multiple identities.",
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

    platforms = {str(a.get("platform", "")).lower() for a in accounts}
    oos = platforms & {"craigslist", "nextdoor", "meetup"}
    if oos:
        score += 8
        signals.append({
            "signal_type": "out_of_state",
            "severity": "medium",
            "detail": f"Active on platforms suggesting possible relocation: {', '.join(sorted(oos))}.",
            "source": "osint_engine",
        })

    legal_keywords = {"arrest", "mugshot", "inmate", "offender", "warrant", "felony", "conviction"}
    for acct in accounts:
        profile_text = _json.dumps(acct.get("profile_data") or {}).lower()
        found = legal_keywords & set(profile_text.split())
        if found:
            score += 15
            signals.append({
                "signal_type": "criminal_record_mention",
                "severity": "high",
                "detail": (
                    f"Criminal/legal keywords found on {acct.get('platform', 'unknown')}: "
                    f"{', '.join(sorted(found))}."
                ),
                "source": acct.get("source", "osint_engine"),
            })
            break

    if total == 0:
        score += 12
        signals.append({
            "signal_type": "social_inactivity",
            "severity": "medium",
            "detail": "No social media presence found — subject may be using aliases.",
            "source": "osint_engine",
        })

    return min(score, 40), signals


def assess_maigret_quality(stderr: str, stdout: str = "") -> Dict[str, Any]:
    """Heuristic quality from Maigret console output."""
    import re
    text = f"{stderr or ''}\n{stdout or ''}"
    reasons: List[str] = []
    degraded = False

    ratios = {}
    for m in re.finditer(
        r'Too many errors of type "([^"]+)"\s*\(([0-9.]+)%\)', text, re.I,
    ):
        kind = m.group(1).lower()
        pct = float(m.group(2)) / 100.0
        ratios[kind] = pct

    access = max(
        (v for k, v in ratios.items() if "access denied" in k or "forbidden" in k),
        default=0.0,
    )
    bot = max(
        (v for k, v in ratios.items() if "bot" in k or "cloudflare" in k or "just a moment" in k),
        default=0.0,
    )

    if access >= DEGRADED_ACCESS_DENIED_RATIO:
        degraded = True
        reasons.append(f"high access-denied rate ({access:.0%})")
    if bot >= DEGRADED_BOT_RATIO:
        degraded = True
        reasons.append(f"high bot/challenge rate ({bot:.0%})")

    return {"degraded": degraded, "reasons": reasons, "error_ratios": ratios}
