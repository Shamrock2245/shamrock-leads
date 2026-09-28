"""
Single source of truth for which sureties Shamrock can write paperwork for.

Rule (2026-09-27): an unknown OR inactive surety must FAIL CLOSED. Nothing in
the paperwork / intake path may silently turn an unrecognised surety label
into OSI. A *missing* surety on an intake is fine (staff pick it at Write
Bond); a missing surety at packet / print time is a validation error.

Active today (have DocuSeal templates + local blanks): OSI, Palmetto.
Listed but INACTIVE ("coming soon", no template): Lexington National,
Roche Surety, Universal, Bankers Surety. The Write Bond picker shows them
greyed out via GET /api/paperwork/sureties.

HOW TO ACTIVATE A SURETY (e.g. "lexington")
  1. DocuSeal: build the carrier's combined packet template on
     sign.shamrockbailbonds.biz using the canonical field names
     (defendant_*, indemnitor_*, coindemnitor_*, poa_*, bond_*).
  2. Env (VPS .env): DOCUSEAL_TEMPLATE_ID_<ID upper>, e.g.
     DOCUSEAL_TEMPLATE_ID_LEXINGTON=<template id>.
  3. POA inventory: set `poa_prefixes` below to the carrier's POA prefixes
     and load its powers into `poa_inventory` with surety_id=<id>.
  4. Drive: set `drive_folder_label` (folder is created on first upload
     under the Completed Bonds root).
  5. Local blanks (print/offline): add templates/<id>/ PDFs.
  6. Flip `active` to True, add a test, deploy.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

# Ordered: the Write Bond picker renders in this order.
SURETY_REGISTRY: Dict[str, Dict[str, Any]] = {
    "osi": {
        "label": "OSI (O'Shaughnahill Surety & Insurance)",
        "short": "OSI",
        "active": True,
        "template_env": ("DOCUSEAL_TEMPLATE_ID_OSI", "DOCUSEAL_TEMPLATE_ID"),
        "poa_prefixes": ("OSI",),
        "drive_folder_label": "OSI Appearance Bonds",
        "website": "",
    },
    "palmetto": {
        "label": "Palmetto Surety Corporation",
        "short": "Palmetto",
        "active": True,
        "template_env": ("DOCUSEAL_TEMPLATE_ID_PALMETTO",),
        "poa_prefixes": (),
        "drive_folder_label": "Palmetto Appearance Bonds",
        "website": "",
    },
    "lexington": {
        "label": "Lexington National Insurance Corporation",
        "short": "Lexington National",
        "active": False,
        "template_env": ("DOCUSEAL_TEMPLATE_ID_LEXINGTON",),
        "poa_prefixes": (),
        "drive_folder_label": "Lexington National Appearance Bonds",
        "website": "https://lexingtonnational.com/bail-bonds/",
    },
    "roche": {
        "label": "Roche Surety and Casualty",
        "short": "Roche Surety",
        "active": False,
        "template_env": ("DOCUSEAL_TEMPLATE_ID_ROCHE",),
        "poa_prefixes": (),
        "drive_folder_label": "Roche Surety Appearance Bonds",
        "website": "https://www.rochesurety.com/",
    },
    "universal": {
        "label": "Universal Fire & Casualty (Universal Bail)",
        "short": "Universal",
        "active": False,
        "template_env": ("DOCUSEAL_TEMPLATE_ID_UNIVERSAL",),
        "poa_prefixes": (),
        "drive_folder_label": "Universal Appearance Bonds",
        "website": "https://universalbail.com/",
    },
    "bankers": {
        "label": "Bankers Surety",
        "short": "Bankers Surety",
        "active": False,
        "template_env": ("DOCUSEAL_TEMPLATE_ID_BANKERS",),
        "poa_prefixes": (),
        "drive_folder_label": "Bankers Surety Appearance Bonds",
        "website": "https://bankerssurety.com/",
    },
}

# Exact, case-insensitive aliases only. No fuzzy matching: fuzzy matching is
# how an unknown surety ends up on the wrong carrier's forms.
_ALIASES = {
    "osi": "osi",
    "palmetto": "palmetto",
    "lexington": "lexington",
    "lexington national": "lexington",
    "roche": "roche",
    "roche surety": "roche",
    "universal": "universal",
    "bankers": "bankers",
    "bankers surety": "bankers",
}

# Back-compat tuple used by older call sites: ACTIVE sureties only.
SUPPORTED_SURETIES: tuple[str, ...] = tuple(k for k, v in SURETY_REGISTRY.items() if v["active"])
DRIVE_FOLDER_LABELS = {k: v["drive_folder_label"] for k, v in SURETY_REGISTRY.items() if v["active"]}


class UnsupportedSuretyError(ValueError):
    """Missing (when required), unknown, or inactive surety."""

    def __init__(self, surety: str, code: str = "unsupported_surety"):
        self.surety = surety
        self.code = code
        active = ", ".join(SURETY_REGISTRY[k]["short"] for k in SUPPORTED_SURETIES)
        if code == "surety_required":
            msg = f"A surety must be selected ({active}) before paperwork."
        elif code == "surety_inactive":
            label = SURETY_REGISTRY.get(surety, {}).get("short", surety)
            msg = (
                f"{label} is listed but not active yet (no paperwork template). "
                f"Active: {active}."
            )
        else:
            msg = f"Surety '{surety}' is not supported for paperwork. Active: {active}."
        super().__init__(msg)


def normalize_surety(raw: object) -> str:
    """Lower/trim and map exact aliases. Unknown values are returned as-is
    (lowercased) so callers can reject them; missing returns ''."""
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    return _ALIASES.get(text, text)


def is_known_surety(raw: object) -> bool:
    return normalize_surety(raw) in SURETY_REGISTRY


def is_supported_surety(raw: object) -> bool:
    """True only for ACTIVE sureties (the ones we can generate paperwork for)."""
    s = normalize_surety(raw)
    return bool(s in SURETY_REGISTRY and SURETY_REGISTRY[s]["active"])


def _error_for(s: str) -> UnsupportedSuretyError:
    if s in SURETY_REGISTRY and not SURETY_REGISTRY[s]["active"]:
        return UnsupportedSuretyError(s, code="surety_inactive")
    return UnsupportedSuretyError(s)


def optional_surety(raw: object) -> Optional[str]:
    """None when missing, canonical id when active, raises when unknown/inactive."""
    s = normalize_surety(raw)
    if not s:
        return None
    if not is_supported_surety(s):
        raise _error_for(s)
    return s


def require_surety(raw: object) -> str:
    """For packet / template / PDF paths: must be present AND active."""
    s = normalize_surety(raw)
    if not s:
        raise UnsupportedSuretyError("", code="surety_required")
    if not is_supported_surety(s):
        raise _error_for(s)
    return s


def template_id_for(raw: object) -> Optional[str]:
    """DocuSeal template id for an ACTIVE surety, else None (fail closed)."""
    s = normalize_surety(raw)
    if not is_supported_surety(s):
        return None
    for env_key in SURETY_REGISTRY[s]["template_env"]:
        tid = (os.getenv(env_key) or "").strip()
        if tid:
            return tid
    return None


def drive_folder_label(raw: object) -> Optional[str]:
    """Drive folder label for ID / bond uploads; None for unknown/inactive."""
    s = normalize_surety(raw)
    if not is_supported_surety(s):
        return None
    return SURETY_REGISTRY[s]["drive_folder_label"]


def picker_options() -> List[Dict[str, Any]]:
    """Rows for the Write Bond surety picker. Inactive rows render greyed out."""
    rows = []
    for sid, meta in SURETY_REGISTRY.items():
        has_template = bool(template_id_for(sid))
        rows.append({
            "id": sid,
            "label": meta["label"],
            "short": meta["short"],
            "active": bool(meta["active"]),
            "selectable": bool(meta["active"]),
            "template_configured": has_template,
            "status": "active" if meta["active"] else "coming_soon",
            "reason": "" if meta["active"] else "Coming soon — no paperwork template yet",
            "website": meta.get("website") or "",
        })
    return rows
