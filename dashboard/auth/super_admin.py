"""
Super Admin Identity — ShamrockLeads

Canonical super-admin email always receives full admin privileges when
authenticated on the Super CRM dashboard.

Aligned with:
  - shamrock-bail-portal-site/src/backend/super-admin.js
  - shamrock-bail-school/lib/auth.ts
  - shamrock-node-red docs/SUPER_ADMIN.md
"""
from __future__ import annotations

import os
from typing import Iterable

# Hardcoded — never depend solely on env for the primary operator account.
SUPER_ADMIN_EMAILS: frozenset[str] = frozenset(
    {
        "admin@shamrockbailbonds.biz",
    }
)

PRIMARY_SUPER_ADMIN = "admin@shamrockbailbonds.biz"


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def _env_admin_emails() -> set[str]:
    raw = os.getenv("ADMIN_EMAILS", "") or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def all_admin_emails() -> set[str]:
    """Super admins ∪ ADMIN_EMAILS env allowlist."""
    return set(SUPER_ADMIN_EMAILS) | _env_admin_emails()


def is_super_admin_email(email: str | None) -> bool:
    return normalize_email(email) in SUPER_ADMIN_EMAILS


def is_admin_email(email: str | None) -> bool:
    """True if email is super-admin or listed in ADMIN_EMAILS."""
    return normalize_email(email) in all_admin_emails()


def resolve_role_for_email(email: str | None, default: str = "staff") -> str:
    """Return 'admin' for super/admin allowlist, else default."""
    if is_admin_email(email):
        return "admin"
    return default
