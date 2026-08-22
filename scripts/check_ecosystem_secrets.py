#!/usr/bin/env python3
"""
Shamrock Ecosystem — Shared Secrets & Configuration Auditor
===========================================================
Validates that required environment keys and secrets exist across:
  - shamrock-leads             (.env)
  - shamrock-bail-portal-site  (Wix Secrets + GAS Script Properties)
  - shamrock-bail-school       (.env.local)
  - shamrock-telegram-app      (Netlify environment)

Truthful Three-State Reporting:
  1. VERIFIED [✅]: Key present with valid non-placeholder value and valid fingerprint.
  2. MISSING  [❌]: Required key is missing from a present environment configuration.
  3. UNVERIFIED / NOT-PROVEN [⚪/⚠️]: Target environment file or remote store is intentionally
     absent from this local checkout. A clean clone does not fake green.

Never prints raw secret values — only key names, fingerprints, and validation status.

Usage:
  python scripts/check_ecosystem_secrets.py
  python scripts/check_ecosystem_secrets.py --strict
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Repo discovery ───────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
LEADS_ROOT = SCRIPT_DIR.parent
SOFTWARE_ROOT = LEADS_ROOT.parent  # shamrock-active-software/


def find_repo(name: str) -> Optional[Path]:
    candidates = [
        SOFTWARE_ROOT / name,
        LEADS_ROOT.parent / name,
        Path.home() / "Desktop" / "shamrock-active-software" / name,
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def load_env_file(path: Path) -> Dict[str, str]:
    """Parse KEY=VALUE env file; ignore comments and empty lines."""
    out: Dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key:
            out[key] = val
    return out


def fingerprint(value: str) -> str:
    """Stable short SHA256 hash for equality checks without revealing secrets."""
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


# ── Manifests ────────────────────────────────────────────────────────────────

LEADS_CRITICAL = [
    "MONGODB_URI",
    "MONGODB_DB_NAME",
    "SECRET_KEY",
    "DASHBOARD_PIN",
    "GAS_API_KEY",
    "GAS_WEB_APP_URL",
]

LEADS_RECOMMENDED = [
    "WIX_WEBHOOK_SECRET",
    "WIX_BLOG_API_KEY",
    "WIX_SITE_ID",
    "PORTAL_BASE_URL",
    "DASHBOARD_PUBLIC_URL",
    "SLACK_WEBHOOK_LEADS",
    "SLACK_WEBHOOK_ARRESTS",
    "SLACK_WEBHOOK_ERRORS",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM_NUMBER",
    "DOCUSEAL_URL",
    "DOCUSEAL_API_KEY",
    "DOCUSEAL_TEMPLATE_ID_OSI",
    "DOCUSEAL_TEMPLATE_ID_PALMETTO",
    "BLUEBUBBLES_URL_0178",
    "BLUEBUBBLES_PASSWORD_0178",
    "OPENAI_API_KEY",
    "MEMO_API_KEY",
    "GOOGLE_GMAIL_REFRESH_TOKEN",
    "GMAIL_PUBSUB_AUDIENCE",
    "GMAIL_PUBSUB_SERVICE_ACCOUNT_EMAIL",
    "GMAIL_PUBSUB_SUBSCRIPTION",
    "GMAIL_MONITORED_MAILBOX",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "COMPLETED_BONDS_FOLDER_ID",
    "SWIPESIMPLE_PAYMENT_LINK",
    "OSINT_WORKER_KEY",
    "TRAPE_SERVER_URL",
    "HUNTER_API_KEY",
]

SCHOOL_CRITICAL = [
    "GAS_WEBHOOK_URL",
    "SESSION_SECRET",
    "GAS_API_KEY",
]

SCHOOL_RECOMMENDED = [
    "NEXT_PUBLIC_GAS_URL",
    "ADMIN_EMAILS",
    "NEXT_PUBLIC_MEET_LINK",
]

PORTAL_DOCUMENTED = [
    "GAS_API_KEY",
    "WIX_API_KEY / WIX_WEBHOOK_SECRET",
    "DOCUSEAL_API_KEY / template IDs",
    "TWILIO_*",
    "OPENAI_API_KEY",
    "MEMO_API_KEY",
    "ELEVENLABS_*",
    "TELEGRAM_BOT_TOKEN",
    "SLACK_*",
]


def check_keys(
    label: str,
    env_file_present: bool,
    env: Dict[str, str],
    critical: List[str],
    recommended: List[str],
) -> Tuple[int, int, int, List[str]]:
    """
    Return (verified_count, critical_missing, unverified_absent, lines for report).
    """
    lines: List[str] = []
    verified_count = 0
    crit_miss = 0
    unverified_absent = 0

    lines.append(f"\n{'═' * 60}")
    lines.append(f"  {label}")
    lines.append(f"{'═' * 60}")

    if not env_file_present:
        lines.append("  ⚠️  File absent in this checkout — values are UNVERIFIED / NOT-PROVEN")
        lines.append("\n  CRITICAL KEYS (Unverified Local Absent):")
        for key in critical:
            lines.append(f"    ⚪ {key}  [NOT-PROVEN — local file absent]")
            unverified_absent += 1
        lines.append("\n  RECOMMENDED KEYS (Unverified Local Absent):")
        for key in recommended:
            lines.append(f"    ⚪ {key}  [NOT-PROVEN — local file absent]")
        return verified_count, crit_miss, unverified_absent, lines

    lines.append("\n  CRITICAL")
    for key in critical:
        val = env.get(key, "")
        present = bool(val and not re.match(r"^<.*>$|^\.\.\.$|^your_|^sk-\.\.\.", val, re.I))
        if present:
            lines.append(f"    ✅ {key}  (fp:{fingerprint(val)})")
            verified_count += 1
        else:
            lines.append(f"    ❌ {key}  MISSING")
            crit_miss += 1

    lines.append("\n  RECOMMENDED")
    for key in recommended:
        val = env.get(key, "")
        present = bool(val and not re.match(r"^<.*>$|^\.\.\.$|^your_", val, re.I))
        if present:
            lines.append(f"    ✅ {key}  (fp:{fingerprint(val)})")
        else:
            lines.append(f"    ⚪ {key}  not set")

    return verified_count, crit_miss, unverified_absent, lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Shamrock Ecosystem Secrets & Config Checker")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any present file is missing critical keys",
    )
    parser.add_argument(
        "--leads-env",
        type=Path,
        default=None,
        help="Path to leads .env (default: <leads>/.env)",
    )
    parser.add_argument(
        "--school-env",
        type=Path,
        default=None,
        help="Path to school .env.local",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON summary",
    )
    args = parser.parse_args()

    portal = find_repo("shamrock-bail-portal-site")
    school = find_repo("shamrock-bail-school")

    leads_env_path = args.leads_env or (LEADS_ROOT / ".env")
    school_env_path = args.school_env or (
        (school / ".env.local") if school else Path("/nonexistent")
    )

    leads_present = leads_env_path.is_file()
    school_present = school_env_path.is_file()

    leads_env = load_env_file(leads_env_path)
    school_env = load_env_file(school_env_path) if school_present else {}

    report: List[str] = [
        "☘️  Shamrock Ecosystem Secrets & Configuration Audit",
        f"    Software root: {SOFTWARE_ROOT}",
        f"    Leads env:     {leads_env_path} {'[PRESENT]' if leads_present else '[ABSENT IN CHECKOUT]'}",
        f"    School env:    {school_env_path} {'[PRESENT]' if school_present else '[ABSENT IN CHECKOUT]'}",
        f"    Portal repo:   {portal or 'remote store only'}",
    ]

    total_verified = 0
    total_crit_missing = 0
    total_unverified = 0

    v, c, u, lines = check_keys(
        f"shamrock-leads  ({leads_env_path})",
        leads_present,
        leads_env,
        LEADS_CRITICAL,
        LEADS_RECOMMENDED,
    )
    total_verified += v
    total_crit_missing += c
    total_unverified += u
    report.extend(lines)

    v, c, u, lines = check_keys(
        f"shamrock-bail-school  ({school_env_path})",
        school_present,
        school_env,
        SCHOOL_CRITICAL,
        SCHOOL_RECOMMENDED,
    )
    total_verified += v
    total_crit_missing += c
    total_unverified += u
    report.extend(lines)

    report.append(f"\n{'═' * 60}")
    report.append("  shamrock-bail-portal-site  (Wix Secrets + GAS Script Properties)")
    report.append(f"{'═' * 60}")
    report.append("  ☁️  Remote Cloud Secret Stores (Verify via console / live API probe):")
    for key in PORTAL_DOCUMENTED:
        report.append(f"    ☁️  {key}")
    if portal:
        rotation = portal / "SECRETS_ROTATION_GUIDE.md"
        report.append(
            f"\n  Rotation guide: {rotation if rotation.is_file() else 'SECRETS_ROTATION_GUIDE.md'}"
        )

    # Cross-repo equality
    report.append(f"\n{'═' * 60}")
    report.append("  SHARED KEY ALIGNMENT (Fingerprint Comparison)")
    report.append(f"{'═' * 60}")

    gas_leads = fingerprint(leads_env.get("GAS_API_KEY", ""))
    gas_school = fingerprint(school_env.get("GAS_API_KEY", ""))
    if gas_leads and gas_school:
        if gas_leads == gas_school:
            report.append("    ✅ GAS_API_KEY  leads ↔ school  MATCH")
        else:
            report.append("    ❌ GAS_API_KEY  leads ↔ school  MISMATCH — fix before go-live")
            total_crit_missing += 1
    elif gas_leads or gas_school:
        report.append(
            "    ⚪ GAS_API_KEY  present in only one local store "
            f"(leads={'yes' if gas_leads else 'no'}, school={'yes' if gas_school else 'no'})"
        )
    else:
        report.append("    ⚪ GAS_API_KEY  unverified in local checkouts (not present in local env)")

    wix_secret = leads_env.get("WIX_WEBHOOK_SECRET") or leads_env.get("GAS_API_KEY")
    if wix_secret:
        report.append(
            f"    ✅ Wix intake webhook auth material present (fp:{fingerprint(wix_secret)})"
        )
    elif leads_present:
        report.append("    ❌ Neither WIX_WEBHOOK_SECRET nor GAS_API_KEY set for intake webhooks")
        total_crit_missing += 1
    else:
        report.append("    ⚪ Wix intake auth unverified (leads .env absent)")

    report.append(f"\n{'─' * 60}")
    report.append(f"  Summary Statistics:")
    report.append(f"    • Verified Present     : {total_verified}")
    report.append(f"    • Critical Missing     : {total_crit_missing}")
    report.append(f"    • Unverified / Absent  : {total_unverified}")

    if total_crit_missing > 0:
        report.append("  Result: ❌ Critical missing keys in present configuration")
    elif total_unverified > 0 and total_verified == 0:
        report.append("  Result: ⚪ UNVERIFIED / NOT-PROVEN (Clean checkout without production .env)")
    else:
        report.append("  Result: ✅ Present configuration verified with valid fingerprints")
    report.append(f"{'─' * 60}\n")

    if args.json:
        out_data = {
            "verified_present": total_verified,
            "critical_missing": total_crit_missing,
            "unverified_absent": total_unverified,
            "status": "fail" if total_crit_missing > 0 else ("unverified" if total_unverified > 0 and total_verified == 0 else "pass"),
        }
        print(json.dumps(out_data, indent=2))
        return 1 if (args.strict and total_crit_missing > 0) else 0

    print("\n".join(report))

    if args.strict and total_crit_missing > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
