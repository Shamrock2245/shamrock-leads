"""
ShamrockLeads — match an office-line (BlueBubbles) phone number to CRM parties
==============================================================================

Used by the BlueBubbles webhook to attach inbound/outbound texts to every case a
phone number belongs to.  Sources (only fields that already exist in code today):

  prospective_bonds (status == "active", same filter the webhook always used)
      indemnitor.phone, indemnitor_phone        -> role "indemnitor"
      indemnitors[].phone                       -> role "co_indemnitor"
      defendant_phone                           -> role "defendant"
  active_bonds (status not in CLOSED_ACTIVE_BOND_STATUSES)
      indemnitor_phone, indemnitor.phone        -> role "indemnitor"
      indemnitors[].phone, co_indemnitor_phone,
      coindemnitor_phone                        -> role "co_indemnitor"
      defendant_phone, phone (legacy defendant) -> role "defendant"
  intake_queue (status not in CLOSED_INTAKE_STATUSES)
      indemnitor_phone, indemnitor.phone        -> role "indemnitor"
      defendant.phone                           -> role "defendant"

Mongo is only used as a coarse regex prefilter (stored phones come in many
formats: "+12395550101", "(239) 555-0101", "239-555-0101"...).  Every hit is
re-verified in Python on the normalised last-10 digits, so a partial/extension
collision can never produce a match.

Ambiguity: matches are grouped into *logical* parties keyed on
``(case key, party class)`` where the case key is the booking number when one
exists (so the same indemnitor on the prospective card and on the resulting
active bond for the same booking is ONE party), and party class collapses
indemnitor/co-indemnitor.  More than one logical party => ``ambiguous=True``;
callers must never auto-pick one.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Tuple

logger = logging.getLogger(__name__)

# Terminal / void statuses: a closed bond is not an "active bond" for matching.
# forfeited is intentionally NOT excluded (forfeiture recovery still texts).
CLOSED_ACTIVE_BOND_STATUSES = frozenset({
    "void", "voided", "VOID", "expired", "exonerated", "surrendered",
    "discharged", "closed", "cancelled", "canceled", "cancelled_exonerated",
})
CLOSED_INTAKE_STATUSES = frozenset({"archived", "dismissed", "rejected", "deleted"})

_PER_COLLECTION_LIMIT = 25

# (collection, base filter, [(field path, role)])
_SOURCES: Tuple[Tuple[str, Dict[str, Any], Tuple[Tuple[str, str], ...]], ...] = (
    (
        "prospective_bonds",
        {"status": "active"},
        (
            ("indemnitor.phone", "indemnitor"),
            ("indemnitor_phone", "indemnitor"),
            ("indemnitors.phone", "co_indemnitor"),
            ("defendant_phone", "defendant"),
        ),
    ),
    (
        "active_bonds",
        {"status": {"$nin": sorted(CLOSED_ACTIVE_BOND_STATUSES)}},
        (
            ("indemnitor_phone", "indemnitor"),
            ("indemnitor.phone", "indemnitor"),
            ("indemnitors.phone", "co_indemnitor"),
            ("co_indemnitor_phone", "co_indemnitor"),
            ("coindemnitor_phone", "co_indemnitor"),
            ("defendant_phone", "defendant"),
            ("phone", "defendant"),
        ),
    ),
    (
        "intake_queue",
        {"status": {"$nin": sorted(CLOSED_INTAKE_STATUSES)}},
        (
            ("indemnitor_phone", "indemnitor"),
            ("indemnitor.phone", "indemnitor"),
            ("defendant.phone", "defendant"),
        ),
    ),
)


def last10_digits(raw: Any) -> str:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


def _phone_regex(last10: str) -> str:
    """Digits of the number with optional non-digits between each (format-agnostic)."""
    return r"\D*".join(re.escape(d) for d in last10)


def _values_at(doc: Any, path: str) -> List[Any]:
    """Resolve a dotted path, fanning out over lists (Mongo semantics)."""
    parts = path.split(".")
    current: List[Any] = [doc]
    for part in parts:
        nxt: List[Any] = []
        for item in current:
            if isinstance(item, list):
                for sub in item:
                    if isinstance(sub, dict) and part in sub:
                        nxt.append(sub[part])
            elif isinstance(item, dict) and part in item:
                nxt.append(item[part])
        current = nxt
    out: List[Any] = []
    for v in current:
        if isinstance(v, list):
            out.extend(v)
        else:
            out.append(v)
    return out


def _party_class(role: str) -> str:
    return "defendant" if role == "defendant" else "indemnitor"


def _booking_of(collection: str, doc: Dict[str, Any]) -> str:
    if collection == "intake_queue":
        defendant = doc.get("defendant") if isinstance(doc.get("defendant"), dict) else {}
        raw = (
            doc.get("defendant_booking_number")
            or doc.get("matched_booking_number")
            or defendant.get("bookingNumber")
            or doc.get("booking_number")
            or ""
        )
    else:
        raw = doc.get("booking_number") or ""
    return str(raw).strip()


def _name_for(collection: str, doc: Dict[str, Any], party_class: str) -> str:
    ind = doc.get("indemnitor") if isinstance(doc.get("indemnitor"), dict) else {}
    if party_class == "defendant":
        return str(doc.get("defendant_name") or "").strip()
    name = (
        doc.get("indemnitor_name")
        or ind.get("name")
        or " ".join(p for p in (ind.get("firstName"), ind.get("lastName")) if p)
        or ""
    )
    return str(name).strip()


def _case_id(collection: str, doc: Dict[str, Any]) -> str:
    if collection == "active_bonds":
        return str(doc.get("Bond_Case_ID") or doc.get("bond_case_id") or doc.get("_id") or "")
    if collection == "intake_queue":
        return str(doc.get("intake_id") or doc.get("_id") or "")
    return str(doc.get("_id") or "")


async def find_party_matches(phone: Any, *, get_collection=None) -> Dict[str, Any]:
    """Find every CRM party whose stored phone equals ``phone``.

    Returns::

        {
          "matches":   [ {collection, doc_id, case_id, booking_number, roles, party_class,
                          name, status, intake_id, _doc}, ... ],
          "parties":   [ {key, booking_number, party_class, match_indexes}, ... ],
          "ambiguous": bool,   # > 1 logical party
        }

    ``_doc`` is the raw Mongo document (needed by the prospective-bond agent
    brain); strip it with :func:`public_matches` before persisting.
    """
    if get_collection is None:
        from dashboard.extensions import get_collection as _gc
        get_collection = _gc

    last10 = last10_digits(phone)
    empty = {"matches": [], "parties": [], "ambiguous": False}
    if not last10:
        return empty

    pattern = _phone_regex(last10)
    matches: List[Dict[str, Any]] = []
    for collection, base_filter, fields in _SOURCES:
        query = dict(base_filter)
        query["$or"] = [{path: {"$regex": pattern}} for path, _ in fields]
        try:
            cursor = get_collection(collection).find(query).limit(_PER_COLLECTION_LIMIT)
            docs = await cursor.to_list(length=_PER_COLLECTION_LIMIT)
        except Exception as exc:
            logger.warning("[bb_match] %s lookup failed: %s", collection, type(exc).__name__)
            continue
        for doc in docs or []:
            if not isinstance(doc, dict):
                continue
            roles: List[str] = []
            hit_fields: List[str] = []
            for path, role in fields:
                if any(last10_digits(v) == last10 for v in _values_at(doc, path)):
                    if role not in roles:
                        roles.append(role)
                    hit_fields.append(path)
            if not roles:
                continue  # regex prefilter false positive
            # A primary indemnitor repeated in indemnitors[] is still the indemnitor.
            if "indemnitor" in roles and "co_indemnitor" in roles:
                roles.remove("co_indemnitor")
            for pclass in sorted({_party_class(r) for r in roles}):
                matches.append({
                    "collection": collection,
                    "doc_id": str(doc.get("_id") or ""),
                    "case_id": _case_id(collection, doc),
                    "booking_number": _booking_of(collection, doc),
                    "roles": [r for r in roles if _party_class(r) == pclass],
                    "party_class": pclass,
                    "fields": [f for f, r in fields if f in hit_fields and _party_class(r) == pclass],
                    "name": _name_for(collection, doc, pclass),
                    "status": str(doc.get("status") or ""),
                    "intake_id": str(doc.get("intake_id") or ""),
                    "_doc": doc,
                })

    parties = _group_parties(matches)
    return {"matches": matches, "parties": parties, "ambiguous": len(parties) > 1}


def _group_parties(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse matches that are the same person on the same case."""
    # Intake rows without a booking inherit the case key of a bond promoted from them.
    intake_to_booking = {
        m["intake_id"]: m["booking_number"]
        for m in matches
        if m["collection"] != "intake_queue" and m["intake_id"] and m["booking_number"]
    }
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for idx, m in enumerate(matches):
        booking = m["booking_number"]
        if not booking and m["collection"] == "intake_queue" and m["intake_id"] in intake_to_booking:
            booking = intake_to_booking[m["intake_id"]]
        case_key = f"booking:{booking.upper()}" if booking else f"{m['collection']}:{m['doc_id']}"
        key = (case_key, m["party_class"])
        grp = groups.setdefault(key, {
            "key": f"{case_key}|{m['party_class']}",
            "booking_number": booking,
            "party_class": m["party_class"],
            "match_indexes": [],
        })
        grp["match_indexes"].append(idx)
    return list(groups.values())


def public_matches(matches: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Matches safe to persist on imessage_outreach (no raw docs)."""
    return [{k: v for k, v in m.items() if k != "_doc"} for m in matches]
