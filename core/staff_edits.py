"""Staff edits survive rescrapes (all counties).

One place decides which arrest fields belong to staff and how a scraped
update is merged around them. Every path that writes source data onto an
existing ``arrests`` doc runs its ``$set`` through
:func:`protect_scraped_update`; every staff edit path records provenance
with :func:`staff_bond_marker` / :func:`staff_charges_marker`.

Provenance (``staff_edits`` subdoc, additive, no backfill)::

    staff_edits.bond     = {"amount": float, "type": str, "at", "by", "source"}
    staff_edits.charges  = {"removed": [desc], "baseline": [desc], "at", "by", "source"}

Charge rows a staff member saved carry ``"source": "staff"``; rows the
writer appends from a later scrape carry ``"source": "scraped"``.

Existing records are read under the legacy markers, without rewriting prod:

* ``bond_override: True`` (update-bond-amount, update-lead-details) or
  ``last_checked_mode == "MANUAL_CHARGE_BONDS"`` (update-charge-bonds) with a
  **positive** ``bond_amount`` is a staff bond. A legacy zero is ambiguous
  (earlier rescrapes reset staff amounts to 0.0 but left the flag), so it is
  not protected and hydrates as unknown.
* ``last_checked_mode == "MANUAL_CHARGE_BONDS"`` means the top-level
  ``charge_details`` rows are staff rows.

When a legacy marker is used, the protected write also records the matching
``staff_edits`` marker (``inferred_from``) so provenance stays durable after
``last_checked_mode`` moves on. Scraped values that lose to staff values are
kept in ``scraped_*`` fields so staff can see source drift.
"""
from __future__ import annotations

import copy
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

MANUAL_CHARGE_MODE = "MANUAL_CHARGE_BONDS"
BOND_FIELDS = ("bond_amount", "bond_amount_raw", "total_bond_amount", "bond_type")
CHARGE_FIELDS = ("charges", "charge_details")
# Projection for the pre-write read of existing docs.
PROVENANCE_PROJECTION = {
    "_id": 0, "state": 1, "county": 1, "booking_number": 1, "record_key": 1,
    "staff_edits": 1, "bond_override": 1, "last_checked_mode": 1,
    "bond_amount": 1, "bond_type": 1, "charges": 1, "charge_details": 1,
}
# Filter matching docs that may carry staff provenance.
PROVENANCE_FILTER = {
    "$or": [
        {"staff_edits": {"$exists": True}},
        {"bond_override": True},
        {"last_checked_mode": MANUAL_CHARGE_MODE},
    ]
}
_SPLIT_RE = re.compile(r"[|\n;]+")
# Scraper placeholders for "no charges listed" (never a real charge row).
_PLACEHOLDER_CHARGES = {"UNKNOWN", "N/A", "NONE", "-"}


def norm_charge(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().upper()


def _money(val: Any) -> float:
    if val is None or (isinstance(val, str) and not val.strip()):
        return 0.0
    try:
        return float(re.sub(r"[^\d.\-]", "", str(val)) or 0)
    except (TypeError, ValueError):
        return 0.0


def charges_from_text(text: Any) -> List[str]:
    return [p.strip() for p in _SPLIT_RE.split(str(text or "")) if p.strip()]


def _row_desc(row: Dict[str, Any]) -> str:
    return str(row.get("charge") or row.get("description") or "").strip()


# ── Provenance ───────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class StaffProvenance:
    bond: bool = False
    bond_amount: Optional[float] = None
    bond_type: str = ""
    bond_inferred_from: str = ""
    charges: bool = False
    charges_inferred_from: str = ""
    removed: Tuple[str, ...] = ()
    baseline: Tuple[str, ...] = ()

    @property
    def any(self) -> bool:
        return self.bond or self.charges


def staff_provenance(doc: Optional[Dict[str, Any]]) -> StaffProvenance:
    doc = doc if isinstance(doc, dict) else {}
    edits = doc.get("staff_edits") if isinstance(doc.get("staff_edits"), dict) else {}
    bond_meta = edits.get("bond") if isinstance(edits.get("bond"), dict) else None
    charge_meta = edits.get("charges") if isinstance(edits.get("charges"), dict) else None
    manual_mode = doc.get("last_checked_mode") == MANUAL_CHARGE_MODE

    bond = False
    amount: Optional[float] = None
    btype = ""
    inferred = ""
    if bond_meta is not None and bond_meta.get("amount") is not None:
        bond, amount = True, _money(bond_meta.get("amount"))
        btype = str(bond_meta.get("type") or doc.get("bond_type") or "")
    elif (doc.get("bond_override") or manual_mode) and _money(doc.get("bond_amount")) > 0:
        bond, amount = True, _money(doc.get("bond_amount"))
        btype = str(doc.get("bond_type") or "")
        inferred = MANUAL_CHARGE_MODE if manual_mode else "bond_override"

    charges = charge_meta is not None
    c_inferred = ""
    if not charges and manual_mode and isinstance(doc.get("charge_details"), list) and doc["charge_details"]:
        charges, c_inferred = True, MANUAL_CHARGE_MODE
    removed = tuple((charge_meta or {}).get("removed") or ())
    baseline = tuple((charge_meta or {}).get("baseline") or ())
    if charges and not baseline and c_inferred:
        baseline = tuple(_row_desc(r) for r in doc.get("charge_details") or [] if isinstance(r, dict))
    return StaffProvenance(
        bond=bond, bond_amount=amount, bond_type=btype, bond_inferred_from=inferred,
        charges=charges, charges_inferred_from=c_inferred, removed=removed, baseline=baseline,
    )


def staff_bond_value(doc: Optional[Dict[str, Any]]) -> Optional[float]:
    """Staff-set bond (a staff $0 is ``0.0``), or ``None`` when staff set none."""
    prov = staff_provenance(doc)
    return prov.bond_amount if prov.bond else None


# ── Staff edit markers (used by the dashboard edit endpoints) ───────────────
def _now_iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


def staff_bond_marker(
    amount: float, *, bond_type: str = "", by: str = "", source: str = "", now: Optional[datetime] = None
) -> Dict[str, Any]:
    """``$set`` fields recording a staff bond (a $0 stays a known $0)."""
    return {
        "staff_edits.bond": {
            "amount": float(amount),
            "type": bond_type or "",
            "at": _now_iso(now),
            "by": by or "dashboard_user",
            "source": source or "dashboard",
        }
    }


def staff_charges_marker(
    existing: Optional[Dict[str, Any]],
    new_rows: List[Dict[str, Any]],
    *,
    by: str = "",
    source: str = "",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """``$set`` fields recording a staff charge-row edit.

    ``removed`` accumulates charges the staff member dropped (so a rescrape
    does not re-add them); ``baseline`` is the scraped charge list staff were
    looking at the first time they edited (so a renamed charge is not re-added).
    """
    existing = existing if isinstance(existing, dict) else {}
    prov = staff_provenance(existing)
    before = [
        _row_desc(r) for r in (existing.get("charge_details") or []) if isinstance(r, dict)
    ] or charges_from_text(existing.get("charges"))
    after = [_row_desc(r) for r in new_rows if isinstance(r, dict)]
    after_counts = Counter(norm_charge(d) for d in after)
    removed = Counter(norm_charge(d) for d in prov.removed)
    for d, n in (Counter(norm_charge(d) for d in before) - after_counts).items():
        removed[d] += n
    for d in list(removed):  # re-added by staff -> no longer removed
        removed[d] = max(0, removed[d] - after_counts.get(d, 0))
    baseline = list(prov.baseline) if prov.baseline else before
    return {
        "staff_edits.charges": {
            "removed": sorted(removed.elements()),
            "baseline": baseline,
            "at": _now_iso(now),
            "by": by or "dashboard_user",
            "source": source or "dashboard",
        }
    }


def mark_staff_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{**r, "source": "staff"} for r in rows if isinstance(r, dict)]


def staff_rows_from_text(text: str, existing_rows: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Charge rows for a staff-edited charges string, keeping per-row fields
    (bond, case, POA) from existing rows with the same description."""
    pool: Dict[str, List[Dict[str, Any]]] = {}
    for r in existing_rows or []:
        if isinstance(r, dict):
            pool.setdefault(norm_charge(_row_desc(r)), []).append(r)
    rows = []
    for desc in charges_from_text(text):
        prior = pool.get(norm_charge(desc)) or []
        base = dict(prior.pop(0)) if prior else {"bond_amount": None, "bond_type": "", "case_number": ""}
        base.update({"charge": desc, "description": desc, "source": "staff"})
        rows.append(base)
    return rows


# ── Merge a scraped update around staff values ──────────────────────────────
def _scraped_rows(set_doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = set_doc.get("charge_details")
    if not rows and isinstance(set_doc.get("extra"), dict):
        rows = set_doc["extra"].get("charge_details")
    if isinstance(rows, list) and rows:
        out = []
        for r in rows:
            if isinstance(r, dict) and _row_desc(r):
                out.append(dict(r))
            elif isinstance(r, str) and r.strip():
                out.append({"charge": r.strip(), "description": r.strip()})
        return out
    return [
        {"charge": c, "description": c}
        for c in charges_from_text(set_doc.get("charges"))
        if norm_charge(c) not in _PLACEHOLDER_CHARGES
    ]


def merge_charge_rows(
    existing_rows: List[Dict[str, Any]], scraped_rows: List[Dict[str, Any]], prov: StaffProvenance
) -> List[Dict[str, Any]]:
    """Staff rows as saved, plus scraped charges staff have not seen or removed."""
    staff_rows = [
        r for r in (existing_rows or []) if isinstance(r, dict) and r.get("source") != "scraped"
    ]
    staff_n = Counter(norm_charge(_row_desc(r)) for r in staff_rows)
    removed_n = Counter(norm_charge(d) for d in prov.removed)
    baseline_n = Counter(norm_charge(d) for d in prov.baseline)
    seen: Counter = Counter()
    added = []
    for r in scraped_rows:
        key = norm_charge(_row_desc(r))
        seen[key] += 1
        covered = max(staff_n[key] + removed_n[key], baseline_n[key])
        if seen[key] > covered:
            row = {k: v for k, v in r.items() if k != "source"}
            row.setdefault("description", _row_desc(r))
            row["charge"] = _row_desc(r)
            row["source"] = "scraped"
            added.append(row)
    return staff_rows + added


def protect_scraped_update(
    set_doc: Dict[str, Any],
    existing: Optional[Dict[str, Any]],
    *,
    record: Any = None,
    now: Optional[datetime] = None,
) -> Tuple[Dict[str, Any], StaffProvenance]:
    """Return a copy of a scraped ``$set`` that cannot overwrite staff values.

    * Staff bond: ``bond_amount`` / ``bond_amount_raw`` / ``total_bond_amount``
      / ``bond_type`` are dropped from the ``$set``; scraped values go to
      ``scraped_bond_amount`` / ``scraped_bond_amount_raw`` /
      ``scraped_bond_type``. When ``record`` (an ArrestRecord) is given and the
      update re-scores, the score is recomputed with the staff bond.
    * Staff charges: ``charge_details`` becomes staff rows + new scraped
      charges; ``charges`` is rebuilt from that; scraped values go to
      ``scraped_charges`` / ``scraped_charge_details``.
    * Docs with no staff provenance pass through unchanged.
    """
    prov = staff_provenance(existing)
    out = dict(set_doc)
    if not prov.any or not existing:
        return out, prov
    stamp = _now_iso(now)

    if prov.bond:
        scraped = {k: out.pop(k) for k in BOND_FIELDS if k in out}
        if "bond_amount" in scraped:
            out["scraped_bond_amount"] = scraped["bond_amount"]
        if "bond_amount_raw" in scraped:
            out["scraped_bond_amount_raw"] = scraped["bond_amount_raw"]
        elif "bond_amount" in scraped:
            out["scraped_bond_amount_raw"] = str(scraped["bond_amount"])
        if "bond_type" in scraped:
            out["scraped_bond_type"] = scraped["bond_type"]
        if scraped:
            out["scraped_bond_at"] = stamp
        if prov.bond_inferred_from:
            out["staff_edits.bond"] = {
                "amount": prov.bond_amount, "type": prov.bond_type, "at": stamp,
                "by": "inferred", "source": "legacy", "inferred_from": prov.bond_inferred_from,
            }
        if record is not None and "lead_score" in out:
            try:
                from scoring.lead_scorer import LeadScorer

                staff_rec = copy.copy(record)
                staff_rec.Bond_Amount = f"{prov.bond_amount:.2f}" if prov.bond_amount else "0"
                if prov.bond_type:
                    staff_rec.Bond_Type = prov.bond_type
                LeadScorer().score_and_update(staff_rec)
                out["scraped_lead_score"] = out.get("lead_score")
                out["lead_score"] = staff_rec.Lead_Score
                out["lead_status"] = staff_rec.Lead_Status
            except Exception:  # noqa: BLE001 - keep the existing score rather than the scraped one
                out.pop("lead_score", None)
                out.pop("lead_status", None)

    extra_rows = isinstance(out.get("extra"), dict) and bool(out["extra"].get("charge_details"))
    if prov.charges and (any(k in out for k in CHARGE_FIELDS) or extra_rows):
        scraped_rows = _scraped_rows(out)
        merged = merge_charge_rows(existing.get("charge_details") or [], scraped_rows, prov)
        if "charges" in out:
            out["scraped_charges"] = out["charges"]
        out["scraped_charge_details"] = scraped_rows
        out["charge_details"] = merged
        out["charges"] = " | ".join(_row_desc(r) for r in merged)
        out["scraped_charges_at"] = stamp
    if prov.charges and prov.charges_inferred_from:
        # Durable marker, so provenance survives last_checked_mode moving on.
        out["staff_edits.charges"] = {
            "removed": list(prov.removed), "baseline": list(prov.baseline), "at": stamp,
            "by": "inferred", "source": "legacy", "inferred_from": prov.charges_inferred_from,
        }
    return out, prov


def fetch_provenance_docs(collection: Any, keys: Iterable[Tuple[str, str, str]],
                          key_field: str = "booking_number") -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    """``{(state, county, key): doc}`` for docs that may carry staff edits.

    ``key_field`` is ``record_key`` once RECORD_KEY_MODE=on (core/record_key.py).
    """
    by_scope: Dict[Tuple[str, str], List[str]] = {}
    for state, county, booking in keys:
        by_scope.setdefault((state, county), []).append(booking)
    found: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for (state, county), bookings in by_scope.items():
        query = {"state": state, "county": county, key_field: {"$in": bookings}, **PROVENANCE_FILTER}
        for doc in collection.find(query, PROVENANCE_PROJECTION):
            found[(doc.get("state"), doc.get("county"), doc.get(key_field))] = doc
    return found
