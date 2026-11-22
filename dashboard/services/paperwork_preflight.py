"""Paperwork Perfection Pre-Flight Gate.

Evaluates a candidate DocuSeal + wet-ink Appearance Bond payload across 5
automated quality checks before staff issue a signing session:

1. ``verbatim_charges`` — exact character parity against arrest roster text and
   DocuSeal template charge capacity.
2. ``statutory_math`` — Florida statutory 10% / $100-per-charge minimum premium
   and bond math validation.
3. ``role_identity`` — role-scoped identity checks (no Defendant/Indemnitor
   DL# or Name+DOB cross-contamination).
4. ``compliance_poa`` — explicit active surety (OSI/Palmetto), validated match/case
   binding, and POA tier coverage.
5. ``appearance_bond`` — required wet-ink appearance bond print readiness fields.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from dashboard.services.bond_packet_start import poa_assignment_block
from dashboard.services.charge_verbatim import (
    ChargeCapacityError,
    fit_charges_for_template,
    resolve_verbatim_charge_rows,
)
from dashboard.services.surety_registry import is_supported_surety

FL_PREMIUM_RATE = 0.10
FL_MIN_PREMIUM_PER_CHARGE = 100.0

PLACEHOLDER_CHARGE_RE = re.compile(
    r"^(?:\s*|arrest\s+charge|charge|tbd|pending|unknown|n/?a)$",
    re.IGNORECASE,
)

# Codes enforced as hard 422 blocks during packet_builder_finalize in addition
# to existing binding/POA/capacity checks.
HARD_FINALIZE_BLOCK_CODES = frozenset(
    {
        "identity_dl_collision",
        "identity_person_collision",
        "premium_below_statutory_minimum",
        "verbatim_charge_drift",
    }
)


def _to_float(raw: Any) -> float:
    if raw is None or raw == "":
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    cleaned = re.sub(r"[^0-9.\-]", "", str(raw))
    if not cleaned:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _text(val: Any) -> str:
    return str(val or "").strip()


def _party_name(party: Optional[Mapping[str, Any]], fallback: Any = "") -> str:
    if isinstance(party, Mapping):
        direct = _text(party.get("name") or party.get("full_name"))
        if direct:
            return direct
        first = _text(party.get("first_name") or party.get("firstName"))
        last = _text(party.get("last_name") or party.get("lastName"))
        combined = " ".join(p for p in (first, last) if p).strip()
        if combined:
            return combined
    return _text(fallback)


def _norm_dl(raw: Any) -> str:
    cleaned = re.sub(r"[^A-Z0-9]", "", _text(raw).upper())
    return cleaned if len(cleaned) >= 5 else ""


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", _text(name).lower()).strip()


def _norm_dob(raw: Any) -> str:
    s = _text(raw)
    if not s:
        return ""
    digits = re.sub(r"\D", "", s)
    return digits if len(digits) >= 6 else s.lower()


def compute_fl_statutory_min_premium(
    bond_amount: float,
    charge_rows: Sequence[Any],
) -> float:
    """Compute the Florida statutory minimum bail premium (10% or $100/bond)."""
    per_charge_amounts: List[float] = []
    for row in charge_rows or ():
        amt = _to_float(getattr(row, "bond_amount", None))
        if amt <= 0 and isinstance(row, Mapping):
            amt = _to_float(
                row.get("bond_amount")
                or row.get("bondAmount")
                or row.get("amount")
            )
        if amt > 0:
            per_charge_amounts.append(amt)

    if per_charge_amounts and len(per_charge_amounts) == len(list(charge_rows or ())):
        total = sum(max(amt * FL_PREMIUM_RATE, FL_MIN_PREMIUM_PER_CHARGE) for amt in per_charge_amounts)
        return round(total, 2)

    if bond_amount > 0:
        return round(max(bond_amount * FL_PREMIUM_RATE, FL_MIN_PREMIUM_PER_CHARGE), 2)

    return 0.0


def _collect_indemnitors(bond_data: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    raw_list = bond_data.get("indemnitors")
    if isinstance(raw_list, list):
        rows = [r for r in raw_list if isinstance(r, Mapping)]
        if rows:
            return rows
    ind = bond_data.get("indemnitor")
    if isinstance(ind, Mapping) and ind:
        return [ind]
    return [
        {
            "name": bond_data.get("indemnitor_name") or "",
            "email": bond_data.get("indemnitor_email") or "",
            "phone": bond_data.get("indemnitor_phone") or "",
            "dl": bond_data.get("indemnitor_dl") or "",
            "dob": bond_data.get("indemnitor_dob") or "",
        }
    ]


def evaluate_paperwork_preflight(
    *,
    bond_data: Mapping[str, Any],
    arrest_doc: Optional[Mapping[str, Any]] = None,
    poa_doc: Optional[Mapping[str, Any]] = None,
    surety_id: Optional[str] = None,
    include_defendant: bool = True,
    self_indemnitor: bool = False,
    template: str = "docuseal",
) -> Dict[str, Any]:
    """Run all 5 perfection checks and return a structured pre-flight report."""
    data = bond_data if isinstance(bond_data, Mapping) else {}
    resolved_surety = _text(surety_id or data.get("surety_id")).lower()
    is_self = bool(self_indemnitor or data.get("self_indemnitor"))

    blocking_issues: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    checks: Dict[str, Dict[str, Any]] = {}

    def _add_issue(check: str, code: str, message: str, *, blocking: bool = True) -> None:
        item = {"check": check, "code": code, "message": message}
        if blocking:
            blocking_issues.append(item)
        else:
            warnings.append(item)

    # ── 1. Verbatim Charges ──────────────────────────────────────────────────
    v_details: List[str] = []
    v_fail = False
    v_warn = False

    packet_rows = resolve_verbatim_charge_rows(data)
    if not packet_rows:
        v_fail = True
        msg = "No charges present on packet payload."
        v_details.append(msg)
        _add_issue("verbatim_charges", "missing_charges", msg, blocking=True)
    else:
        bad_placeholders = [
            r.charge for r in packet_rows if PLACEHOLDER_CHARGE_RE.match(r.charge or "")
        ]
        if bad_placeholders:
            v_fail = True
            msg = f"Placeholder charge text rejected: {bad_placeholders[0]!r}."
            v_details.append(msg)
            _add_issue("verbatim_charges", "placeholder_charge_text", msg, blocking=True)

        if isinstance(arrest_doc, Mapping) and arrest_doc:
            arrest_rows = resolve_verbatim_charge_rows(arrest_doc)
            if arrest_rows:
                pkt_texts = [r.charge for r in packet_rows]
                arr_texts = [r.charge for r in arrest_rows]
                if pkt_texts != arr_texts:
                    v_fail = True
                    msg = (
                        "Packet charges do not match county/arrest roster text character-for-character "
                        f"({len(pkt_texts)} packet vs {len(arr_texts)} arrest charge(s))."
                    )
                    v_details.append(msg)
                    _add_issue("verbatim_charges", "verbatim_charge_drift", msg, blocking=True)
                else:
                    v_details.append(f"Matched {len(pkt_texts)} verbatim arrest charge(s) byte-for-byte.")

        if is_supported_surety(resolved_surety):
            try:
                placement = fit_charges_for_template(
                    packet_rows,
                    surety_id=resolved_surety,
                    template=template,
                )
                for note in getattr(placement, "layout_notes", ()) or ():
                    note_str = str(note)
                    v_details.append(note_str)
                    if re.search(r"cannot|overflow|clip|exceed|pointer", note_str, re.IGNORECASE):
                        v_warn = True
                        _add_issue("verbatim_charges", "charge_layout_note", note_str, blocking=False)
            except ChargeCapacityError as exc:
                v_fail = True
                v_details.append(str(exc))
                _add_issue("verbatim_charges", "charge_capacity_exceeded", str(exc), blocking=True)

    checks["verbatim_charges"] = {
        "status": "fail" if v_fail else ("warn" if v_warn else "pass"),
        "summary": (
            f"{len(packet_rows)} verbatim charge(s) verified"
            if not v_fail
            else (v_details[0] if v_details else "Charge check failed")
        ),
        "details": v_details or [f"{len(packet_rows)} charge(s) present."],
    }

    # ── 2. Statutory Math ────────────────────────────────────────────────────
    m_details: List[str] = []
    m_fail = False
    m_warn = False

    bond_amount = _to_float(data.get("bond_amount"))
    premium_amount = _to_float(data.get("premium_amount") if data.get("premium_amount") not in (None, "") else data.get("premium"))
    statutory_min = compute_fl_statutory_min_premium(bond_amount, packet_rows)

    if bond_amount <= 0:
        m_fail = True
        msg = "Bond amount must be greater than $0.00."
        m_details.append(msg)
        _add_issue("statutory_math", "bond_amount_missing", msg, blocking=True)
    else:
        m_details.append(f"Bond face amount: ${bond_amount:,.2f} (FL min premium ${statutory_min:,.2f}).")
        if premium_amount > 0:
            if premium_amount + 0.01 < statutory_min:
                m_fail = True
                msg = (
                    f"Premium ${premium_amount:,.2f} is below Florida statutory minimum "
                    f"${statutory_min:,.2f} (10% / $100 minimum per charge)."
                )
                m_details.append(msg)
                _add_issue("statutory_math", "premium_below_statutory_minimum", msg, blocking=True)
            else:
                m_details.append(f"Premium ${premium_amount:,.2f} satisfies statutory minimum.")
        else:
            m_warn = True
            msg = "Premium amount not confirmed on packet yet."
            m_details.append(msg)
            _add_issue("statutory_math", "premium_not_set", msg, blocking=False)

        row_bonds = [_to_float(r.bond_amount) for r in packet_rows if _to_float(r.bond_amount) > 0]
        if row_bonds and len(row_bonds) == len(packet_rows):
            row_sum = round(sum(row_bonds), 2)
            if abs(row_sum - bond_amount) > 0.01:
                m_warn = True
                msg = f"Per-charge bond sum (${row_sum:,.2f}) differs from total bond_amount (${bond_amount:,.2f})."
                m_details.append(msg)
                _add_issue("statutory_math", "charge_bond_sum_mismatch", msg, blocking=False)

    checks["statutory_math"] = {
        "status": "fail" if m_fail else ("warn" if m_warn else "pass"),
        "summary": (
            f"Bond ${bond_amount:,.2f} · Min ${statutory_min:,.2f}"
            if bond_amount > 0
            else "Missing bond amount"
        ),
        "details": m_details,
    }

    # ── 3. Role-Scoped Identity ──────────────────────────────────────────────
    i_details: List[str] = []
    i_fail = False
    i_warn = False

    def_party = data.get("defendant") if isinstance(data.get("defendant"), Mapping) else {}
    def_name = _party_name(def_party, data.get("defendant_name"))
    def_dl = _norm_dl(def_party.get("dl") or data.get("defendant_dl"))
    def_dob = _norm_dob(def_party.get("dob") or data.get("defendant_dob"))
    ind_list = _collect_indemnitors(data)
    primary_ind = ind_list[0] if ind_list else {}
    ind_name = _party_name(primary_ind, data.get("indemnitor_name"))

    if not def_name:
        i_fail = True
        msg = "Defendant full name is required."
        i_details.append(msg)
        _add_issue("role_identity", "missing_defendant_name", msg, blocking=True)
    if not ind_name:
        i_fail = True
        msg = "Primary indemnitor full name is required."
        i_details.append(msg)
        _add_issue("role_identity", "missing_indemnitor_name", msg, blocking=True)

    if not is_self:
        for idx, party in enumerate(ind_list):
            role_label = "Indemnitor" if idx == 0 else f"Co-indemnitor #{idx + 1}"
            p_dl = _norm_dl(party.get("dl") or (data.get("indemnitor_dl") if idx == 0 else ""))
            if def_dl and p_dl and def_dl == p_dl:
                i_fail = True
                msg = f"{role_label} DL# matches Defendant DL# — possible OCR identity overwrite."
                i_details.append(msg)
                _add_issue("role_identity", "identity_dl_collision", msg, blocking=True)

            p_name = _party_name(party, data.get("indemnitor_name") if idx == 0 else "")
            p_dob = _norm_dob(party.get("dob") or (data.get("indemnitor_dob") if idx == 0 else ""))
            if def_name and p_name and def_dob and p_dob:
                if _norm_name(def_name) == _norm_name(p_name) and def_dob == p_dob:
                    i_fail = True
                    msg = f"{role_label} name and DOB match Defendant without self-indemnitor flag."
                    i_details.append(msg)
                    _add_issue("role_identity", "identity_person_collision", msg, blocking=True)

    ind_email = _text(primary_ind.get("email") or data.get("indemnitor_email"))
    ind_phone = _text(primary_ind.get("phone") or data.get("indemnitor_phone"))
    if not ind_email or not ind_phone:
        i_warn = True
        msg = "Primary indemnitor is missing email or phone for digital delivery."
        i_details.append(msg)
        _add_issue("role_identity", "missing_signer_contact", msg, blocking=False)

    if include_defendant:
        def_email = _text(def_party.get("email") or data.get("defendant_email"))
        def_phone = _text(def_party.get("phone") or data.get("defendant_phone"))
        if not def_email or not def_phone:
            i_warn = True
            msg = "Defendant is missing email or phone for electronic signature delivery."
            i_details.append(msg)
            _add_issue("role_identity", "missing_defendant_contact", msg, blocking=False)

    checks["role_identity"] = {
        "status": "fail" if i_fail else ("warn" if i_warn else "pass"),
        "summary": (
            f"Defendant ({def_name or '?'}) and Indemnitor ({ind_name or '?'}) isolated"
            if not i_fail
            else (i_details[0] if i_details else "Identity conflict")
        ),
        "details": i_details or ["Role-scoped party records verified."],
    }

    # ── 4. Compliance & POA ──────────────────────────────────────────────────
    c_details: List[str] = []
    c_fail = False
    c_warn = False

    if not is_supported_surety(resolved_surety):
        c_fail = True
        msg = f"Explicit active surety required (OSI or Palmetto), got {resolved_surety!r}."
        c_details.append(msg)
        _add_issue("compliance_poa", "unsupported_or_missing_surety", msg, blocking=True)

    if _text(data.get("match_status")).lower() != "validated":
        c_fail = True
        msg = "Match must be validated before issuing DocuSeal paperwork."
        c_details.append(msg)
        _add_issue("compliance_poa", "match_not_validated", msg, blocking=True)

    missing_bindings = [
        k for k in ("bond_case_id", "match_id", "case_number", "booking_number", "poa_number")
        if not _text(data.get(k))
    ]
    if missing_bindings:
        c_fail = True
        msg = "Missing authoritative binding field(s): " + ", ".join(missing_bindings)
        c_details.append(msg)
        _add_issue("compliance_poa", "missing_binding_fields", msg, blocking=True)

    if isinstance(poa_doc, Mapping) and poa_doc:
        poa_block = poa_assignment_block(poa_doc, data)
        if poa_block:
            c_fail = True
            c_details.append(poa_block[1])
            _add_issue("compliance_poa", poa_block[0], poa_block[1], blocking=True)
    elif poa_doc is None and _text(data.get("poa_number")):
        c_details.append(f"POA {_text(data.get('poa_number'))} supplied (inventory lookup deferred).")

    if not include_defendant:
        c_warn = True
        msg = "Defendant signing omitted; F.S. § 648 requires defendant signature on every bond."
        c_details.append(msg)
        _add_issue("compliance_poa", "defendant_signing_omitted", msg, blocking=False)

    checks["compliance_poa"] = {
        "status": "fail" if c_fail else ("warn" if c_warn else "pass"),
        "summary": (
            f"Surety {resolved_surety.upper()} · POA {_text(data.get('poa_number')) or '—'}"
            if not c_fail
            else (c_details[0] if c_details else "Compliance check failed")
        ),
        "details": c_details or ["Surety, validated match, and POA binding verified."],
    }

    # ── 5. Wet-Ink Appearance Bond Print Readiness ───────────────────────────
    a_details: List[str] = []
    a_fail = False
    a_warn = False

    missing_print = [
        k for k in ("county", "case_number", "booking_number", "poa_number")
        if not _text(data.get(k))
    ]
    if missing_print:
        a_fail = True
        msg = "Appearance bond missing required field(s): " + ", ".join(missing_print)
        a_details.append(msg)
        _add_issue("appearance_bond", "appearance_bond_missing_fields", msg, blocking=True)
    if not _text(data.get("court_date")):
        a_warn = True
        msg = "Court date is not yet populated for the wet-ink Appearance Bond."
        a_details.append(msg)
        _add_issue("appearance_bond", "missing_court_date", msg, blocking=False)
    facility_val = (
        _text(data.get("facility"))
        or _text(data.get("jail_facility"))
        or _text(data.get("jail_name"))
        or _text(data.get("jail"))
        or _text(def_party.get("facility"))
        or _text(def_party.get("jail_name"))
    )
    if not facility_val:
        a_warn = True
        msg = "Detention facility is blank; verify booking jail before printing."
        a_details.append(msg)
        _add_issue("appearance_bond", "missing_facility", msg, blocking=False)

    checks["appearance_bond"] = {
        "status": "fail" if a_fail else ("warn" if a_warn else "pass"),
        "summary": (
            "Appearance Bond print fields complete"
            if not a_fail and not a_warn
            else ("Ready with optional gaps" if not a_fail else (a_details[0] if a_details else "Not print-ready"))
        ),
        "details": a_details or ["All mandatory wet-ink Appearance Bond fields populated."],
    }

    score = 0
    for check in checks.values():
        if check["status"] == "pass":
            score += 20
        elif check["status"] == "warn":
            score += 15

    return {
        "ready": len(blocking_issues) == 0,
        "score": score,
        "blocking_issues": blocking_issues,
        "warnings": warnings,
        "checks": checks,
    }
