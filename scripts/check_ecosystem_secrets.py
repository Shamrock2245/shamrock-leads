#!/usr/bin/env python3
"""
Shamrock Ecosystem — Shared Secrets Checklist
=============================================
Validates that required environment keys exist across:
  - shamrock-leads        (.env)
  - shamrock-bail-portal-site  (documents required; Wix/GAS are remote)
  - shamrock-bail-school  (.env.local)

Never prints secret values — only key names and status.

Usage:
  python scripts/check_ecosystem_secrets.py
  python scripts/check_ecosystem_secrets.py --strict   # exit 1 on any missing critical

Shared keys (must match across systems when set):
  GAS_API_KEY, WIX_WEBHOOK_SECRET (portal↔leads), SESSION_SECRET (school)
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

# ── Repo discovery ───────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
LEADS_ROOT = SCRIPT_DIR.parent
SOFTWARE_ROOT = LEADS_ROOT.parent  # shamrock-active-software/


def find_repo(name: str) -> Path | None:
    candidates = [
        SOFTWARE_ROOT / name,
        LEADS_ROOT.parent / name,
        Path.home() / "Desktop" / "shamrock-active-software" / name,
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def load_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE env file; ignore comments and empty lines."""
    out: dict[str, str] = {}
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
    """Stable short hash for equality checks without revealing secrets."""
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
    "SIGNNOW_API_TOKEN",
    "SIGNNOW_BASIC_AUTH",
    "BLUEBUBBLES_URL_0178",
    "BLUEBUBBLES_PASSWORD_0178",
    "OPENAI_API_KEY",
    "GOOGLE_GMAIL_REFRESH_TOKEN",
    "SWIPESIMPLE_PAYMENT_LINK",
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

# Portal secrets live in Wix Secrets Manager + GAS Script Properties (not local .env)
PORTAL_DOCUMENTED = [
    "GAS_API_KEY",
    "WIX_API_KEY / WIX_WEBHOOK_SECRET",
    "SIGNNOW_API_KEY / tokens",
    "TWILIO_*",
    "OPENAI_API_KEY",
    "ELEVENLABS_*",
    "TELEGRAM_BOT_TOKEN",
    "SLACK_*",
]

# Keys that should be identical across systems (when present in multiple places)
SHARED_EQUALITY = [
    ("GAS_API_KEY", "leads", "school"),
]


def check_keys(
    label: str,
    env: dict[str, str],
    critical: list[str],
    recommended: list[str],
) -> tuple[int, int, list[str]]:
    """Return (critical_missing, recommended_missing, lines for report)."""
    lines: list[str] = []
    crit_miss = 0
    rec_miss = 0

    lines.append(f"\n{'═' * 60}")
    lines.append(f"  {label}")
    lines.append(f"{'═' * 60}")

    if not env and "not found" not in label.lower():
        lines.append("  ⚠️  No env file loaded (missing or empty)")

    lines.append("\n  CRITICAL")
    for key in critical:
        val = env.get(key, "")
        present = bool(val and not re.match(r"^<.*>$|^\.\.\.$|^your_|^sk-\.\.\.", val, re.I))
        if present:
            lines.append(f"    ✅ {key}  (fp:{fingerprint(val)})")
        else:
            lines.append(f"    ❌ {key}  MISSING")
            crit_miss += 1

    lines.append("\n  RECOMMENDED")
    for key in recommended:
        val = env.get(key, "")
        present = bool(val and not re.match(r"^<.*>$|^\.\.\.$|^your_", val, re.I))
        if present:
            lines.append(f"    ✅ {key}")
        else:
            lines.append(f"    ⚪ {key}  not set")
            rec_miss += 1

    return crit_miss, rec_miss, lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Shamrock ecosystem secrets checklist")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any critical key is missing",
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
    args = parser.parse_args()

    portal = find_repo("shamrock-bail-portal-site")
    school = find_repo("shamrock-bail-school")

    leads_env_path = args.leads_env or (LEADS_ROOT / ".env")
    school_env_path = args.school_env or (
        (school / ".env.local") if school else Path("/nonexistent")
    )

    leads_env = load_env_file(leads_env_path)
    school_env = load_env_file(school_env_path) if school_env_path.is_file() else {}

    report: list[str] = [
        "☘️  Shamrock Ecosystem Secrets Checklist",
        f"    Software root: {SOFTWARE_ROOT}",
        f"    Leads env:     {leads_env_path} {'✓' if leads_env_path.is_file() else '✗ missing'}",
        f"    School env:    {school_env_path} {'✓' if school_env_path.is_file() else '✗ missing'}",
        f"    Portal repo:   {portal or 'not found (Wix Secrets are remote)'}",
    ]

    total_crit = 0
    total_rec = 0

    c, r, lines = check_keys(
        f"shamrock-leads  ({leads_env_path})",
        leads_env,
        LEADS_CRITICAL,
        LEADS_RECOMMENDED,
    )
    total_crit += c
    total_rec += r
    report.extend(lines)

    c, r, lines = check_keys(
        f"shamrock-bail-school  ({school_env_path})",
        school_env,
        SCHOOL_CRITICAL,
        SCHOOL_RECOMMENDED,
    )
    total_crit += c
    total_rec += r
    report.extend(lines)

    report.append(f"\n{'═' * 60}")
    report.append("  shamrock-bail-portal-site  (Wix Secrets + GAS Script Properties)")
    report.append(f"{'═' * 60}")
    report.append("  These are NOT in a local .env — verify in Wix Dashboard and GAS editor:")
    for key in PORTAL_DOCUMENTED:
        report.append(f"    ☐ {key}")
    if portal:
        rotation = portal / "SECRETS_ROTATION_GUIDE.md"
        report.append(
            f"\n  Rotation guide: {rotation if rotation.is_file() else 'SECRETS_ROTATION_GUIDE.md'}"
        )

    # Cross-repo equality
    report.append(f"\n{'═' * 60}")
    report.append("  SHARED KEY ALIGNMENT (fingerprint compare)")
    report.append(f"{'═' * 60}")

    gas_leads = fingerprint(leads_env.get("GAS_API_KEY", ""))
    gas_school = fingerprint(school_env.get("GAS_API_KEY", ""))
    if gas_leads and gas_school:
        if gas_leads == gas_school:
            report.append("    ✅ GAS_API_KEY  leads ↔ school  MATCH")
        else:
            report.append("    ❌ GAS_API_KEY  leads ↔ school  MISMATCH — fix before go-live")
            total_crit += 1
    elif gas_leads or gas_school:
        report.append(
            "    ⚪ GAS_API_KEY  only set in one repo "
            f"(leads={'yes' if gas_leads else 'no'}, school={'yes' if gas_school else 'no'})"
        )
    else:
        report.append("    ⚪ GAS_API_KEY  not set in leads or school local env")

    wix_secret = leads_env.get("WIX_WEBHOOK_SECRET") or leads_env.get("GAS_API_KEY")
    if wix_secret:
        report.append(
            f"    ✅ Wix intake webhook auth material present (fp:{fingerprint(wix_secret)})"
        )
    else:
        report.append("    ❌ Neither WIX_WEBHOOK_SECRET nor GAS_API_KEY for intake webhooks")
        total_crit += 1

    # CRM readiness hints
    report.append(f"\n{'═' * 60}")
    report.append("  LEADS SUPER-CRM OPS HINTS")
    report.append(f"{'═' * 60}")
    report.append("    • Run indexes:  python scripts/mongo_indexes.py")
    report.append("    • CRM health:   GET https://leads.shamrockbailbonds.biz/api/crm/health")
    report.append("    • Overview:     GET /api/crm/overview")
    report.append("    • Omnibar:      GET /api/crm/search?q=...")
    report.append("    • Docs:         docs/SUPER_CRM.md  +  docs/ECOSYSTEM.md")

    report.append(f"\n{'─' * 60}")
    report.append(f"  Summary:  {total_crit} critical gaps  ·  {total_rec} recommended unset")
    if total_crit == 0:
        report.append("  Result:   ✅ Critical local keys look set")
    else:
        report.append("  Result:   ❌ Fix critical gaps before production")
    report.append(f"{'─' * 60}\n")

    print("\n".join(report))

    if args.strict and total_crit > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
