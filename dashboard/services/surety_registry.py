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
  Staff can publish one without a code change: Super CRM → Paperwork
  Config → Surety Templates → Add surety. Upload the carrier PDFs, confirm
  canonical mappings, set POA prefixes, preview with sample data, publish.
  Write Bond then uses that immutable version.

  DocuSeal id resolution (do not break production):
    1. Env DOCUSEAL_TEMPLATE_ID_<ID> still wins for registry sureties.
       OSI reads DOCUSEAL_TEMPLATE_ID_OSI then DOCUSEAL_TEMPLATE_ID.
       Palmetto reads DOCUSEAL_TEMPLATE_ID_PALMETTO only.
       Production stays OSI=1, Palmetto=5 while those env vars are set.
    2. Else the active published version's docuseal_template_id.
    Seeded OSI/Palmetto v1 leave that id empty so step 1 is unchanged.

  POA prefixes and inventory for a new carrier are owner input. Publishing
  does not invent them. Listed-but-inactive carriers stay fail-closed until
  a non-seed version is published.
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


def _published_doc(surety_id: str):
    """Active published version, if the onboarding store can be imported."""
    try:
        from dashboard.services.surety_template_store import active_published
    except Exception:
        return None
    return active_published(surety_id)


def is_supported_surety(raw: object) -> bool:
    """True for registry-active sureties and for sureties with a published template.

    A migration seed does not flip a registry-inactive carrier on. Lexington,
    Roche, Universal, and Bankers stay fail-closed until staff publish a version.
    """
    s = normalize_surety(raw)
    if not s:
        return False
    if s in SURETY_REGISTRY and SURETY_REGISTRY[s]["active"]:
        return True
    doc = _published_doc(s)
    if not doc:
        return False
    if s in SURETY_REGISTRY and not SURETY_REGISTRY[s]["active"] and doc.get("migration_seed"):
        return False
    return True


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
    """DocuSeal template id for a surety we can write, else None (fail closed).

    Registry env vars win when set (production OSI=1, Palmetto=5). A published
    version id is used only when no env id is set. Inactive carriers without
    a published version stay None even if their env var is present.
    """
    s = normalize_surety(raw)
    if not is_supported_surety(s):
        return None
    if s in SURETY_REGISTRY:
        for env_key in SURETY_REGISTRY[s]["template_env"]:
            tid = (os.getenv(env_key) or "").strip()
            if tid:
                return tid
    doc = _published_doc(s)
    tid = str((doc or {}).get("docuseal_template_id") or "").strip()
    return tid or None


def drive_folder_label(raw: object) -> Optional[str]:
    """Drive folder label for ID / bond uploads; None for unknown/inactive."""
    s = normalize_surety(raw)
    if not is_supported_surety(s):
        return None
    if s in SURETY_REGISTRY and SURETY_REGISTRY[s]["active"]:
        return SURETY_REGISTRY[s]["drive_folder_label"]
    doc = _published_doc(s)
    label = str((doc or {}).get("drive_folder_label") or "").strip()
    return label or None


def picker_options() -> List[Dict[str, Any]]:
    """Rows for the Write Bond surety picker. Inactive rows render greyed out.

    A published onboarding version makes a listed or new surety selectable.
    Registry `active: False` alone does not.
    """
    rows = []
    seen = set()
    for sid, meta in SURETY_REGISTRY.items():
        seen.add(sid)
        selectable = is_supported_surety(sid)
        has_template = bool(template_id_for(sid)) if selectable else False
        rows.append({
            "id": sid,
            "label": meta["label"],
            "short": meta["short"],
            "active": selectable,
            "selectable": selectable,
            "template_configured": has_template,
            "status": "active" if selectable else "coming_soon",
            "reason": "" if selectable else "Coming soon — no paperwork template yet",
            "website": meta.get("website") or "",
        })
    try:
        from dashboard.services.surety_template_store import list_onboarding
        extras = list_onboarding()
    except Exception:
        extras = []
    for row in extras:
        sid = row.get("surety_id")
        if not sid or sid in seen:
            continue
        if not row.get("published_version"):
            continue
        seen.add(sid)
        selectable = is_supported_surety(sid)
        rows.append({
            "id": sid,
            "label": row.get("label") or sid,
            "short": row.get("label") or sid,
            "active": selectable,
            "selectable": selectable,
            "template_configured": bool(template_id_for(sid)) if selectable else False,
            "status": "active" if selectable else "coming_soon",
            "reason": "" if selectable else "Coming soon — no paperwork template yet",
            "website": "",
        })
    return rows
