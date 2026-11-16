"""
OSINT Report Builder — ShamrockLeads
====================================
One place that turns a stored ``osint_scans`` document into a recovery-agent
report. The PDF export, the CSV export, and their tests all read from
``build_osint_report`` so the three never disagree about what a lead is.

Sections, in the order an agent works a skip-trace:
  1. Case header + search inputs
  2. Recovery leads (locations, contact points, linked handles, vehicle, breaches)
  3. Suggested next steps (deterministic follow-up scans, never auto-run)
  4. Accounts grouped by category
  5. Other entities
  6. Risk signals (advisory only)
  7. Engine run log

Nothing here contacts a subject or sends a message. Findings are leads to
verify, not confirmed facts.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

ENGINE_LABELS: Dict[str, str] = {
    "maigret": "Maigret (username, 3000+ sites)",
    "tookie": "Tookie-OSINT (username sweep)",
    "sherlock": "Sherlock (username cross-check)",
    "blackbird": "Blackbird (username / email)",
    "spiderfoot": "SpiderFoot (entity correlation)",
    "ignorant": "Ignorant (phone registrations)",
    "holehe": "Holehe (email registrations)",
    "ghunt": "GHunt (Google account / Maps)",
    "h8mail": "h8mail (breach sources)",
    "hibf": "HIBF (Flock plate audit logs)",
    "toutatis": "Toutatis (Instagram contact info)",
    "instaloader": "Instaloader (Instagram profile)",
    "exiftool": "ExifTool (photo GPS / camera)",
}

CATEGORY_ORDER = ["social", "dating", "messaging", "professional", "financial", "forum", "gaming", "other"]

_LOCATION_TYPES = {"location", "address"}
_CONTACT_TYPES = {"phone", "email"}


def _norm(v: Any) -> str:
    return " ".join(str(v or "").split())


def _fmt_dt(v: Any) -> str:
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M UTC")
    if isinstance(v, str) and v:
        return v[:19].replace("T", " ")
    return ""


def maps_url_for(value: str = "", lat: Optional[float] = None, lon: Optional[float] = None) -> str:
    if lat is not None and lon is not None:
        return f"https://www.google.com/maps?q={lat},{lon}"
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(value)}" if value else ""


def _known_inputs(params: Dict[str, Any]) -> Dict[str, set]:
    users = {str(u).strip().lower() for u in (params.get("usernames") or []) if str(u).strip()}
    email = str(params.get("email") or "").strip().lower()
    phone_digits = "".join(ch for ch in str(params.get("phone") or "") if ch.isdigit())[-10:]
    return {
        "usernames": users,
        "emails": {email} if email else set(),
        "phones": {phone_digits} if phone_digits else set(),
    }


def _is_new_contact(etype: str, value: str, known: Dict[str, set]) -> bool:
    if etype == "email":
        return value.strip().lower() not in known["emails"]
    if etype == "phone":
        digits = "".join(ch for ch in value if ch.isdigit())[-10:]
        return bool(digits) and digits not in known["phones"]
    return True


def build_osint_report(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Structure a scan document into report sections (pure, no I/O)."""
    params = doc.get("scan_params") or {}
    accounts: List[Dict[str, Any]] = [a for a in (doc.get("accounts") or []) if isinstance(a, dict)]
    entities: List[Dict[str, Any]] = [e for e in (doc.get("entities") or []) if isinstance(e, dict)]
    geo_points: List[Dict[str, Any]] = [g for g in (doc.get("geo_points") or []) if isinstance(g, dict)]
    progress: Dict[str, Any] = doc.get("progress") or {}
    known = _known_inputs(params)

    # Accounts the agent marked irrelevant drop out of the lead sections.
    live_accounts = [a for a in accounts if a.get("relevance") != "irrelevant"]
    live_entities = [e for e in entities if e.get("relevance") != "irrelevant"]

    # ── Header ────────────────────────────────────────────────────────────────
    header = {
        "subject_name": _norm(doc.get("full_name") or params.get("full_name")) or "Unknown subject",
        "subject_type": (_norm(doc.get("subject_type")) or "defendant").capitalize(),
        "subject_id": _norm(doc.get("subject_id")),
        "scan_id": str(doc.get("_id") or doc.get("scan_id") or ""),
        "status": (_norm(doc.get("status")) or "unknown").upper(),
        "created_at": _fmt_dt(doc.get("created_at")),
        "completed_at": _fmt_dt(doc.get("completed_at")),
        "requested_by": _norm(doc.get("scan_requested_by")),
        "engines": list(doc.get("engines_requested") or []),
        "notes": _norm(doc.get("notes")),
    }

    inputs: List[List[str]] = []
    for label, key in (
        ("Full name", "full_name"), ("Date of birth", "dob"), ("Email", "email"),
        ("Phone", "phone"), ("License plate", "license_plate"), ("Photo URL", "image_url"),
    ):
        if params.get(key):
            inputs.append([label, _norm(params[key])])
    if params.get("usernames"):
        inputs.append(["Usernames searched", ", ".join(str(u) for u in params["usernames"])])
    scope = "Deep scan" if params.get("deep_scan") else "Quick scan"
    if params.get("top_sites"):
        scope += f" · Maigret top {params['top_sites']} sites"
    if params.get("second_opinion"):
        scope += " · second opinion"
    inputs.append(["Scan scope", scope])

    # ── Recovery leads ────────────────────────────────────────────────────────
    locations: List[Dict[str, str]] = []
    seen_loc = set()
    for g in geo_points:
        try:
            lat, lon = float(g.get("latitude")), float(g.get("longitude"))
        except (TypeError, ValueError):
            continue
        key = (round(lat, 4), round(lon, 4))
        if key in seen_loc:
            continue
        seen_loc.add(key)
        locations.append({
            "value": _norm(g.get("label")) or f"{lat:.5f}, {lon:.5f}",
            "detail": _norm(g.get("context")) or f"{lat:.5f}, {lon:.5f}",
            "source": _norm(g.get("source")) or "geo",
            "map_url": maps_url_for(lat=lat, lon=lon),
        })
    for e in live_entities:
        if str(e.get("type", "")).lower() in _LOCATION_TYPES:
            val = _norm(e.get("value"))
            if not val or val.lower() in seen_loc:
                continue
            seen_loc.add(val.lower())
            locations.append({
                "value": val,
                "detail": _norm(e.get("context")),
                "source": _norm(e.get("source")),
                "map_url": maps_url_for(val),
            })

    contacts: List[Dict[str, str]] = []
    seen_contact = set()
    for e in live_entities:
        etype = str(e.get("type", "")).lower()
        if etype not in _CONTACT_TYPES:
            continue
        val = _norm(e.get("value"))
        if not val or (etype, val.lower()) in seen_contact:
            continue
        seen_contact.add((etype, val.lower()))
        contacts.append({
            "type": etype,
            "value": val,
            "source": _norm(e.get("source")),
            "context": _norm(e.get("context")),
            "new": "NEW" if _is_new_contact(etype, val, known) else "searched",
        })
    # Toutatis / Instaloader put recovered contact info on profile_data.
    for a in live_accounts:
        pdata = a.get("profile_data") or {}
        for key, etype in (("public_email", "email"), ("obfuscated_email", "email"),
                           ("public_phone", "phone"), ("obfuscated_phone", "phone"),
                           ("email", "email"), ("phone", "phone")):
            val = _norm(pdata.get(key))
            if not val or (etype, val.lower()) in seen_contact:
                continue
            if etype == "email" and "@" not in val:
                continue
            seen_contact.add((etype, val.lower()))
            contacts.append({
                "type": etype,
                "value": val,
                "source": _norm(a.get("source")),
                "context": f"from {a.get('platform', 'profile')} profile",
                "new": "NEW" if _is_new_contact(etype, val, known) else "searched",
            })

    handles: List[Dict[str, Any]] = []
    by_handle: Dict[str, Dict[str, Any]] = {}
    for a in live_accounts:
        u = _norm(a.get("username"))
        if not u:
            continue
        h = by_handle.setdefault(u.lower(), {"handle": u, "platforms": set(), "sources": set()})
        if a.get("platform"):
            h["platforms"].add(str(a["platform"]))
        if a.get("source"):
            h["sources"].add(str(a["source"]))
    for h in sorted(by_handle.values(), key=lambda x: -len(x["platforms"])):
        handles.append({
            "handle": h["handle"],
            "platform_count": len(h["platforms"]),
            "platforms": ", ".join(sorted(h["platforms"])[:6]) + (" …" if len(h["platforms"]) > 6 else ""),
            "sources": ", ".join(sorted(h["sources"])),
            "new": "searched" if h["handle"].lower() in known["usernames"] else "NEW",
        })

    vehicle = [
        {"value": _norm(e.get("value")), "source": _norm(e.get("source")), "context": _norm(e.get("context"))}
        for e in live_entities if str(e.get("source", "")).lower() == "hibf"
    ] + [
        {"value": _norm(a.get("platform")), "source": "hibf", "context": _norm(a.get("url"))}
        for a in live_accounts if str(a.get("source", "")).lower() == "hibf"
    ]
    breaches = [
        {"value": _norm(e.get("value")), "source": _norm(e.get("source")), "context": _norm(e.get("context"))}
        for e in live_entities if str(e.get("type", "")).lower() == "breach"
    ]

    # ── Accounts by category ──────────────────────────────────────────────────
    grouped: Dict[str, List[Dict[str, str]]] = {}
    for a in live_accounts:
        if str(a.get("source", "")).lower() == "hibf":
            continue
        cat = (_norm(a.get("category")) or "other").lower()
        grouped.setdefault(cat, []).append({
            "platform": _norm(a.get("platform")),
            "username": _norm(a.get("username")),
            "url": _norm(a.get("url")),
            "source": _norm(a.get("source")),
            "confidence": _norm(a.get("confidence")) or "found",
            "relevance": _norm(a.get("relevance")) or "unreviewed",
        })
    conf_rank = {"found": 0, "likely": 1, "uncertain": 2}
    accounts_by_category = [
        (cat, sorted(grouped[cat], key=lambda r: (conf_rank.get(r["confidence"], 3), r["platform"].lower())))
        for cat in sorted(grouped, key=lambda c: (CATEGORY_ORDER.index(c) if c in CATEGORY_ORDER else 99, c))
    ]

    used_types = _LOCATION_TYPES | _CONTACT_TYPES | {"breach"}
    other_entities = [
        {
            "type": _norm(e.get("type")),
            "value": _norm(e.get("value")),
            "source": _norm(e.get("source")),
            "module": _norm(e.get("module")),
            "confidence": _norm(e.get("confidence")),
            "context": _norm(e.get("context")),
        }
        for e in live_entities
        if str(e.get("type", "")).lower() not in used_types and str(e.get("source", "")).lower() != "hibf"
    ]

    # ── Engine run log ────────────────────────────────────────────────────────
    engine_log = []
    for eng in header["engines"] or list(progress.keys()):
        info = progress.get(eng) or {}
        found = int(info.get("accounts_found") or 0) + int(info.get("entities_found") or 0)
        note = _norm(info.get("error") or info.get("warning"))
        engine_log.append({
            "engine": ENGINE_LABELS.get(eng, eng),
            "engine_id": eng,
            "status": (_norm(info.get("status")) or "pending").upper(),
            "found": found,
            "note": note,
        })

    # ── Next steps (suggestions only; agents decide what to run) ─────────────
    ran = set(header["engines"])
    steps: List[str] = []
    for c in contacts:
        if c["new"] != "NEW":
            continue
        if c["type"] == "email" and "@" in c["value"]:
            steps.append(f"Run the Email playbook on {c['value']} (Holehe, h8mail, GHunt).")
        elif c["type"] == "phone":
            steps.append(f"Run the Phone playbook on {c['value']} (Ignorant, SpiderFoot).")
    for h in handles:
        if h["new"] == "NEW" and h["platform_count"] >= 2:
            steps.append(f"Run the Alias playbook on @{h['handle']} (seen on {h['platform_count']} platforms).")
    if locations:
        steps.append("Open each location on the map and compare it with the booking address, "
                     "indemnitor addresses, and known associates.")
    insta = [a for a in live_accounts if "instagram" in str(a.get("platform", "")).lower()]
    if insta and not ({"toutatis", "instaloader"} & ran):
        steps.append("An Instagram account was found. Run Instaloader (bio, links) and Toutatis "
                     "(contact info) on that handle.")
    if params.get("email") and "ghunt" not in ran:
        steps.append("GHunt was not run on the email. If the worker is logged in, it can surface "
                     "public Google Maps reviews.")
    failed = [e["engine"] for e in engine_log if e["status"] == "FAILED"]
    if failed:
        steps.append("Re-run failed engines: " + ", ".join(failed) + ".")
    if not live_accounts and not live_entities:
        steps.append("No findings. Try name-derived handles (CLI drawer), a deep scan, or another identifier.")
    # Keep order, drop duplicates, cap the list so it stays readable.
    seen_steps = set()
    next_steps = [s for s in steps if not (s in seen_steps or seen_steps.add(s))][:10]

    signals = [
        {
            "severity": (_norm(s.get("severity")) or "low").upper(),
            "type": _norm(s.get("signal_type")).replace("_", " "),
            "detail": _norm(s.get("detail")),
        }
        for s in (doc.get("risk_signals") or []) if isinstance(s, dict)
    ]

    return {
        "header": header,
        "inputs": inputs,
        "kpis": {
            "accounts": len(live_accounts),
            "entities": len(live_entities),
            "platforms": len({a.get("platform") for a in live_accounts if a.get("platform")}),
            "locations": len(locations),
            "contacts": len(contacts),
            "risk_score": int(doc.get("osint_risk_score") or 0),
        },
        "ai_summary": _norm(doc.get("ai_summary")),
        "leads": {
            "locations": locations,
            "contacts": contacts,
            "handles": handles,
            "vehicle": vehicle,
            "breaches": breaches,
        },
        "next_steps": next_steps,
        "accounts_by_category": accounts_by_category,
        "other_entities": other_entities,
        "signals": signals,
        "engine_log": engine_log,
        "warnings": [_norm(w) for w in (doc.get("warnings") or []) if _norm(w)],
        "hidden_irrelevant": (len(accounts) - len(live_accounts)) + (len(entities) - len(live_entities)),
    }
