"""
Pay-by-card link per intake source — the ONE place to change it.

Owner rule (2026-09-27):
  * Every source gets the same pay-by-card option (website indemnitor
    starting a bond included).
  * A case's OWN SwipeSimple invoice link always wins (share-invoice /
    per-bond link already stored on the case).
  * Otherwise: Telegram (bot + mini-apps) → "Payment Link for Telegram"
    (lnk_07a13eb…); every other source → "Payment Link on Website"
    (lnk_b6bf996f…).

To change a link, set the env var for that source on the VPS (see
SOURCE_ENV below) — no code change needed. To add a source, add one row to
SOURCE_DEFAULTS (and optionally SOURCE_ENV / _SOURCE_ALIASES).

This module only RESOLVES a URL. It never sends anything; all existing
send gates (SWIPESIMPLE_DISPATCH_LIVE, send-once markers, premium
confirmation) are unchanged.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Optional

WEBSITE_LINK = "https://swipesimple.com/links/lnk_b6bf996f4c57bb340a150e297e769abd"
TELEGRAM_LINK = "https://swipesimple.com/links/lnk_07a13eb404d7f3057a56d56d8bb488c8"

# Canonical source → default static link.
SOURCE_DEFAULTS: Dict[str, str] = {
    "default": WEBSITE_LINK,
    "website": WEBSITE_LINK,      # Wix wizards (defendant / indemnitor)
    "telegram": TELEGRAM_LINK,    # Telegram bot + Netlify mini-apps
    "kiosk": WEBSITE_LINK,        # in-office tablet
    "portal": WEBSITE_LINK,       # paperwork.shamrockbailbonds.biz PIN portal
    "manual": WEBSITE_LINK,       # staff walk-in entry
    "shannon": WEBSITE_LINK,      # ElevenLabs voice agent
    "sms": WEBSITE_LINK,          # BlueBubbles / Twilio texts
    "scraper": WEBSITE_LINK,      # sheriff / clerk hydrate
    "email": WEBSITE_LINK,
    "referral": WEBSITE_LINK,
}

# Optional per-source env overrides (first non-empty wins). The legacy
# SWIPESIMPLE_PAYMENT_LINK / SWIPESIMPLE_BOND_PAYMENT_LINK / PAYMENT_LINK
# vars keep working as the override for the "default" (website) link.
SOURCE_ENV: Dict[str, tuple] = {
    "default": ("SWIPESIMPLE_LINK_DEFAULT", "SWIPESIMPLE_PAYMENT_LINK",
                "SWIPESIMPLE_BOND_PAYMENT_LINK", "PAYMENT_LINK"),
    "telegram": ("SWIPESIMPLE_LINK_TELEGRAM",),
}

_SOURCE_ALIASES = {
    "wix": "website", "wix_intake": "website", "wix_webhook": "website",
    "web": "website", "site": "website", "wix_wizard": "website",
    "telegram_bot": "telegram", "telegram_miniapp": "telegram",
    "telegram_mini_app": "telegram", "tg": "telegram", "mini_app": "telegram",
    "in_office": "kiosk", "tablet": "kiosk", "lobby": "kiosk",
    "pin_portal": "portal", "paperwork_portal": "portal",
    "walk_in": "manual", "staff": "manual", "dashboard": "manual",
    "voice": "shannon", "elevenlabs": "shannon", "twilio_voice": "shannon",
    "bluebubbles": "sms", "imessage": "sms", "text": "sms", "twilio_sms": "sms",
    "arrest_scraper": "scraper", "sheriff": "scraper", "clerk": "scraper",
}

# Fields on a case (bond / packet / intake) that hold ITS OWN invoice link.
CASE_LINK_FIELDS = (
    "swipesimple_payment_link",
    "invoice_payment_link",
    "share_invoice_link",
    "payment_link",
)


def normalize_source(source: Optional[str]) -> str:
    s = str(source or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not s:
        return "default"
    if s in SOURCE_DEFAULTS:
        return s
    if s in _SOURCE_ALIASES:
        return _SOURCE_ALIASES[s]
    if s.startswith("telegram"):
        return "telegram"
    if s.startswith("wix"):
        return "website"
    return "default"


def case_invoice_link(case: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not case:
        return None
    for key in CASE_LINK_FIELDS:
        link = str(case.get(key) or "").strip()
        if link.startswith("https://"):
            return link
    return None


def static_link_for_source(source: Optional[str]) -> str:
    src = normalize_source(source)
    for env_key in SOURCE_ENV.get(src, ()):
        v = (os.getenv(env_key) or "").strip()
        if v.startswith("http"):
            return v
    if src not in SOURCE_ENV and src != "default":
        # Sources without their own override follow the default link's
        # override (so one env change moves every non-Telegram source).
        if SOURCE_DEFAULTS.get(src) == WEBSITE_LINK:
            return static_link_for_source("default")
    return SOURCE_DEFAULTS.get(src) or SOURCE_DEFAULTS["default"]


def payment_link_for(source: Optional[str] = None,
                     case: Optional[Mapping[str, Any]] = None) -> str:
    """Case's own invoice link → per-source static link → website link."""
    return case_invoice_link(case) or static_link_for_source(source)


def source_of(case: Optional[Mapping[str, Any]]) -> str:
    """Best-effort intake source from a stored record."""
    if not case:
        return "default"
    for key in ("intake_source", "source", "lead_source", "channel", "origin"):
        v = case.get(key)
        if v:
            return normalize_source(str(v))
    return "default"


def describe() -> Dict[str, str]:
    """Resolved link per source (for staff Settings / docs)."""
    return {src: static_link_for_source(src) for src in SOURCE_DEFAULTS}
