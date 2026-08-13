"""Normalize DocuSeal submitters into indemnitor / defendant sign cards.

Staff and the public portal both need one branded URL per party:
  https://paperwork.shamrockbailbonds.biz/sign/{packet_id}/indemnitor
  https://paperwork.shamrockbailbonds.biz/sign/{packet_id}/defendant

The redirect looks up the live DocuSeal slug. Never log phones / emails.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional

PAPERWORK_PUBLIC_URL = (
    os.getenv("PAPERWORK_PUBLIC_URL")
    or os.getenv("PORTAL_SIGN_BASE_URL")
    or "https://paperwork.shamrockbailbonds.biz"
).rstrip("/")

ROLE_ALIASES = {
    "indemnitor": "indemnitor",
    "ind": "indemnitor",
    "cosigner": "indemnitor",
    "co-signer": "indemnitor",
    "defendant": "defendant",
    "def": "defendant",
    "inmate": "defendant",
    "coindemnitor": "coindemnitor",
    "co-indemnitor": "coindemnitor",
    "co_indemnitor": "coindemnitor",
    "bondsman": "bondsman",
    "agent": "bondsman",
}

ROLE_LABELS = {
    "indemnitor": "Indemnitor",
    "defendant": "Defendant",
    "coindemnitor": "Co-indemnitor",
    "bondsman": "Bondsman",
}

# Prefer client-facing roles in this order when a generic /sign/{id} is opened
_ROLE_PREFERENCE = ("indemnitor", "coindemnitor", "defendant", "bondsman")


def normalize_role(role: Optional[str]) -> str:
    raw = (role or "").strip().lower()
    return ROLE_ALIASES.get(raw, raw or "indemnitor")


def branded_sign_url(packet_id: str, role: str) -> str:
    rid = (packet_id or "").strip()
    return f"{PAPERWORK_PUBLIC_URL}/sign/{rid}/{normalize_role(role)}"


def _digits_phone(raw: Any) -> str:
    return "".join(ch for ch in str(raw or "") if ch.isdigit())[-10:]


def _submitter_sign_url(sub: Dict[str, Any]) -> str:
    for key in ("sign_url", "embed_src", "signing_link"):
        val = sub.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
    slug = sub.get("slug")
    if isinstance(slug, str) and slug:
        if slug.startswith("http"):
            return slug
        host = (os.getenv("DOCUSEAL_URL") or "https://sign.shamrockbailbonds.biz").rstrip("/")
        if not host.startswith("http"):
            host = f"https://{host}"
        return f"{host}/s/{slug}"
    return ""


def party_signers_from_submitters(
    submitters: Optional[Iterable[Dict[str, Any]]],
    *,
    packet_id: str = "",
    indemnitor_name: str = "",
    defendant_name: str = "",
    indemnitor_phone: str = "",
    defendant_phone: str = "",
) -> List[Dict[str, Any]]:
    """Build staff-facing party cards from DocuSeal submitters."""
    parties: List[Dict[str, Any]] = []
    seen = set()
    for sub in submitters or []:
        if not isinstance(sub, dict):
            continue
        role = normalize_role(sub.get("role"))
        if role in seen or role == "bondsman":
            continue
        sign_url = _submitter_sign_url(sub)
        if not sign_url:
            continue
        seen.add(role)
        if role == "defendant":
            name = (sub.get("name") or defendant_name or "").strip()
            phone = _digits_phone(sub.get("phone") or defendant_phone)
        else:
            name = (sub.get("name") or indemnitor_name or "").strip()
            phone = _digits_phone(sub.get("phone") or indemnitor_phone)
        parties.append(
            {
                "role": role,
                "label": ROLE_LABELS.get(role, role.title()),
                "name": name,
                "phone": phone,
                "status": (sub.get("status") or "pending"),
                "sign_url": sign_url,
                "share_url": branded_sign_url(packet_id, role) if packet_id else sign_url,
            }
        )
    return parties


def party_signers_from_packet(packet: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not packet or not isinstance(packet, dict):
        return []
    pid = str(packet.get("packet_id") or packet.get("_id") or "")
    rebuilt = party_signers_from_submitters(
        packet.get("docuseal_submitters") or packet.get("submitters") or [],
        packet_id=pid,
        indemnitor_name=str(packet.get("indemnitor_name") or ""),
        defendant_name=str(packet.get("defendant_name") or ""),
        indemnitor_phone=str(packet.get("indemnitor_phone") or ""),
        defendant_phone=str(packet.get("defendant_phone") or ""),
    )
    if rebuilt:
        return rebuilt
    cached = packet.get("parties")
    if isinstance(cached, list) and cached and all(
        isinstance(p, dict) and p.get("share_url") for p in cached
    ):
        return cached
    return []


def pick_party(
    parties: Iterable[Dict[str, Any]],
    role: Optional[str] = None,
    phone: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    rows = [p for p in parties if isinstance(p, dict)]
    if not rows:
        return None
    if role:
        want = normalize_role(role)
        for p in rows:
            if normalize_role(p.get("role")) == want:
                return p
    digits = _digits_phone(phone)
    if digits:
        for p in rows:
            if _digits_phone(p.get("phone")) == digits:
                return p
    by_role = {normalize_role(p.get("role")): p for p in rows}
    for pref in _ROLE_PREFERENCE:
        if pref in by_role:
            return by_role[pref]
    return rows[0]
