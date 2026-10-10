"""
Missed check-in evidence pack (BailSafe P0 slice A2).

Assembles stored check-in rows into a staff PDF/ZIP. It does not create GPS,
selfies, or timestamps. Randomized check-in windows stay deferred.

Stored shapes this pack reads:
  check_in_log     lat, lng, gps_accuracy_meters, selfie_b64, timestamp
  bond_checkins    gps_lat, gps_lon, gps_accuracy, selfie_thumbnail (stub),
                   optional selfie_path / selfie_ref, checkin_at
  active_bonds     next_checkin_due / next_check_in_due, last check-in,
                   missed_check_ins, checkin_link_last_sent_at
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.booking_identity import public_booking_number, redact_internal_keys

from dashboard.extensions import get_collection
from dashboard.services.identity_media_service import UPLOAD_DIR

logger = logging.getLogger("shamrock.checkin_evidence")

DEFAULT_LIMIT = 20
MAX_LIMIT = 50
SLA_LABEL = "signed bond, check-in not sent"

_SIGNED_PACKET_STATUSES = ("signed", "completed")
_BOOKING_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def clamp_limit(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(n, MAX_LIMIT))


def _iso(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pdf_text(value: Any, limit: int = 180) -> str:
    text = str(value if value is not None else "")
    text = text.replace("\r", " ").replace("\n", " ")
    encoded = text.encode("latin-1", "replace").decode("latin-1")
    return encoded[:limit]


def safe_booking_filename(booking_number: str) -> str:
    # A Miami-Dade internal key is never printed, not even in a filename.
    cleaned = _BOOKING_SAFE.sub("_", public_booking_number(booking_number or "").strip())[:80]
    return cleaned or "booking"


def _row_timestamp(row: dict) -> Optional[str]:
    return _iso(row.get("timestamp") or row.get("checkin_at") or row.get("checked_in_at"))


def decode_selfie_b64(raw: Any) -> Optional[bytes]:
    """Decode a stored selfie payload. Truncated thumbnail stubs are not images."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or len(text) < 100:
        return None
    if text.startswith("data:"):
        parts = text.split(",", 1)
        if len(parts) != 2:
            return None
        text = parts[1]
    try:
        data = base64.b64decode(text, validate=False)
    except Exception:
        return None
    if len(data) < 32:
        return None
    if data[:2] == b"\xff\xd8" or data[:8] == b"\x89PNG\r\n\x1a\n" or data[:4] == b"RIFF":
        return data
    return None


def _read_upload_selfie(path_value: Any) -> Optional[bytes]:
    """Read a selfie only when the stored path is a file under dashboard/uploads."""
    if not isinstance(path_value, str) or not path_value.strip():
        return None
    candidate = Path(path_value)
    try:
        resolved = candidate.resolve()
        resolved.relative_to(UPLOAD_DIR.resolve())
    except (OSError, ValueError):
        return None
    if not resolved.is_file():
        return None
    try:
        data = resolved.read_bytes()
    except OSError:
        return None
    if not data:
        return None
    return data


def normalize_checkin_row(row: dict, *, source: str) -> dict:
    """Map one stored row. Missing GPS or selfie stays null — nothing is filled in."""
    lat = _number(row.get("lat") if row.get("lat") is not None else row.get("gps_lat"))
    lng = _number(row.get("lng") if row.get("lng") is not None else row.get("gps_lon"))
    accuracy = _number(
        row.get("gps_accuracy_meters")
        if row.get("gps_accuracy_meters") is not None
        else row.get("gps_accuracy")
        if row.get("gps_accuracy") is not None
        else row.get("accuracy")
    )
    checkin_id = str(row.get("checkin_id") or row.get("_id") or "").strip()
    stored_ref = str(row.get("selfie_ref") or "").strip()
    stored_path = str(row.get("selfie_path") or "").strip()
    selfie_bytes = decode_selfie_b64(row.get("selfie_b64"))
    if selfie_bytes is None and stored_path:
        selfie_bytes = _read_upload_selfie(stored_path)

    selfie_ref = stored_ref or stored_path
    selfie_file = None
    if selfie_bytes:
        stem = checkin_id or "selfie"
        stem = _BOOKING_SAFE.sub("_", stem)[:60] or "selfie"
        ext = "png" if selfie_bytes[:8] == b"\x89PNG\r\n\x1a\n" else "jpg"
        selfie_file = f"selfies/{stem}.{ext}"
        if not selfie_ref:
            selfie_ref = f"{source}:{stem}"
    elif row.get("selfie_thumbnail"):
        selfie_ref = selfie_ref or "bond_checkins:thumbnail_stub"
    elif row.get("has_selfie") and not selfie_ref:
        selfie_ref = f"{source}:has_selfie_no_bytes"

    return {
        "source": source,
        "checkin_id": checkin_id or None,
        "timestamp": _row_timestamp(row),
        "lat": lat,
        "lng": lng,
        "accuracy": accuracy,
        "status": row.get("status") or None,
        "method": row.get("method") or row.get("source") or None,
        "has_selfie": bool(row.get("has_selfie") or selfie_bytes or selfie_ref),
        "selfie_ref": selfie_ref or None,
        "selfie_file": selfie_file,
        "due_at": _iso(row.get("due_at") or row.get("next_checkin_due")),
        "_selfie_bytes": selfie_bytes,
    }


def schedule_from_bond(bond: Optional[dict], *, now: Optional[datetime] = None) -> dict:
    bond = bond or {}
    next_due = _iso(bond.get("next_check_in_due") or bond.get("next_checkin_due"))
    last_checkin = _iso(
        bond.get("last_checkin_at") or bond.get("last_check_in") or bond.get("last_checkin")
    )
    missed = bond.get("missed_check_ins")
    try:
        missed_n = int(missed) if missed is not None and missed != "" else 0
    except (TypeError, ValueError):
        missed_n = 0
    overdue_flag = bond.get("check_in_overdue")
    overdue = bool(overdue_flag) if isinstance(overdue_flag, bool) else None
    if next_due:
        try:
            due_dt = datetime.fromisoformat(next_due.replace("Z", "+00:00"))
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=timezone.utc)
            clock = now or datetime.now(timezone.utc)
            overdue = due_dt < clock
        except ValueError:
            pass
    freq = bond.get("check_in_frequency_days")
    try:
        freq_n = int(freq) if freq is not None and freq != "" else None
    except (TypeError, ValueError):
        freq_n = None
    return {
        "check_in_required": bool(bond.get("check_in_required")),
        "frequency_days": freq_n,
        "next_due": next_due,
        "last_checkin": last_checkin,
        "missed_check_ins": missed_n,
        "check_in_overdue": overdue,
        "checkin_link_last_sent_at": _iso(bond.get("checkin_link_last_sent_at")),
        "checkin_enabled_at": _iso(bond.get("checkin_enabled_at")),
    }


def build_evidence_manifest(
    *,
    booking_number: str,
    bond: Optional[dict],
    check_in_logs: list[dict],
    bond_checkins: Optional[list[dict]] = None,
    limit: int = DEFAULT_LIMIT,
    generated_at: Optional[str] = None,
) -> dict:
    """Last N stored rows plus due/missed fields. Empty logs stay an empty list."""
    n = clamp_limit(limit)
    rows: list[dict] = []
    for raw in check_in_logs or []:
        stored_booking = str(raw.get("booking_number") or "").strip()
        if stored_booking and stored_booking != str(booking_number):
            continue
        rows.append(normalize_checkin_row(raw, source="check_in_log"))
    for raw in bond_checkins or []:
        if raw.get("booking_number") and str(raw.get("booking_number")) != str(booking_number):
            continue
        rows.append(normalize_checkin_row(raw, source="bond_checkins"))

    def _sort_key(item: dict) -> str:
        return item.get("timestamp") or ""

    logs = [row for row in rows if row.get("source") == "check_in_log"]
    portal = [row for row in rows if row.get("source") != "check_in_log"]
    logs.sort(key=_sort_key, reverse=True)
    portal.sort(key=_sort_key, reverse=True)
    # Last N from each store. A busy portal history must not drop check_in_log rows.
    selected = logs[:n] + portal[:n]
    selected.sort(key=_sort_key, reverse=True)
    public_rows = []
    for item in selected:
        public = {k: v for k, v in item.items() if not k.startswith("_")}
        public_rows.append(public)

    empty = len(public_rows) == 0
    bond = bond or {}
    return {
        # Printed in the PDF/ZIP: an internal Miami-Dade key prints blank.
        "booking_number": public_booking_number(booking_number),
        "defendant_name": bond.get("defendant_name") or bond.get("Defendant_Name") or "",
        "county": bond.get("county") or "",
        "case_number": bond.get("case_number") or bond.get("Case_Number") or "",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "limit": n,
        "empty": empty,
        "empty_reason": "no_check_in_logs" if empty else None,
        "randomized_windows": "deferred",
        "schedule": schedule_from_bond(bond),
        "entries": redact_internal_keys(public_rows),
        "_files": [
            {"name": item["selfie_file"], "bytes": item["_selfie_bytes"]}
            for item in selected
            if item.get("selfie_file") and item.get("_selfie_bytes")
        ],
    }


def render_evidence_pdf(manifest: dict) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, _pdf_text("Shamrock Bail Bonds - Check-in evidence pack"), ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, _pdf_text(
        f"Booking {manifest.get('booking_number') or '—'}  |  "
        f"{manifest.get('defendant_name') or 'Name not on bond'}  |  "
        f"Generated {str(manifest.get('generated_at') or '')[:19]}"
    ), ln=True)
    pdf.ln(2)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(0, 4, _pdf_text(
        "Coordinates and photos below are copied from stored check-in rows. "
        "Blank GPS or selfie means that field was not stored. "
        "Randomized check-in windows are not applied in this pack.",
        400,
    ))
    pdf.ln(2)

    schedule = manifest.get("schedule") or {}
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Due and missed", ln=True)
    pdf.set_font("Helvetica", "", 9)
    lines = [
        f"Check-in required: {'yes' if schedule.get('check_in_required') else 'no'}",
        f"Frequency days: {schedule.get('frequency_days') if schedule.get('frequency_days') is not None else '—'}",
        f"Next due: {schedule.get('next_due') or '—'}",
        f"Last check-in: {schedule.get('last_checkin') or '—'}",
        f"Missed count: {schedule.get('missed_check_ins') if schedule.get('missed_check_ins') is not None else '—'}",
        f"Overdue: {schedule.get('check_in_overdue') if schedule.get('check_in_overdue') is not None else '—'}",
        f"Enrollment link sent: {schedule.get('checkin_link_last_sent_at') or 'not sent'}",
    ]
    for line in lines:
        pdf.cell(0, 5, _pdf_text(line), ln=True)

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    entries = manifest.get("entries") or []
    if manifest.get("empty") or not entries:
        pdf.cell(0, 6, "Check-in log", ln=True)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5, "No check-in logs on file for this booking.")
    else:
        pdf.cell(0, 6, f"Check-in log ({len(entries)})", ln=True)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(38, 5, "Timestamp")
        pdf.cell(28, 5, "Lat")
        pdf.cell(28, 5, "Lon")
        pdf.cell(22, 5, "Source")
        pdf.cell(0, 5, "Selfie ref", ln=True)
        pdf.set_font("Helvetica", "", 8)
        for entry in entries:
            pdf.cell(38, 5, _pdf_text(str(entry.get("timestamp") or "—")[:19], 19))
            lat = entry.get("lat")
            lng = entry.get("lng")
            pdf.cell(28, 5, _pdf_text("—" if lat is None else f"{lat:.5f}", 16))
            pdf.cell(28, 5, _pdf_text("—" if lng is None else f"{lng:.5f}", 16))
            pdf.cell(22, 5, _pdf_text(entry.get("source") or "—", 16))
            pdf.cell(0, 5, _pdf_text(entry.get("selfie_ref") or "—", 48), ln=True)
            extra = []
            if entry.get("accuracy") is not None:
                extra.append(f"accuracy {entry['accuracy']}")
            if entry.get("status"):
                extra.append(f"status {entry['status']}")
            if entry.get("selfie_file"):
                extra.append(f"file {entry['selfie_file']}")
            if extra:
                pdf.set_font("Helvetica", "I", 7)
                pdf.cell(0, 4, _pdf_text("  " + " · ".join(extra), 160), ln=True)
                pdf.set_font("Helvetica", "", 8)
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(0, 4, "Staff use only. Location is consent-based check-in evidence, not continuous tracking.")
    return bytes(pdf.output())


def _public_manifest(manifest: dict) -> dict:
    return {k: v for k, v in manifest.items() if not k.startswith("_")}


def render_evidence_zip(manifest: dict) -> bytes:
    pdf_bytes = render_evidence_pdf(manifest)
    booking = safe_booking_filename(str(manifest.get("booking_number") or "booking"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(_public_manifest(manifest), indent=2, default=str),
        )
        zf.writestr(f"Checkin_Evidence_{booking}.pdf", pdf_bytes)
        seen = set()
        for item in manifest.get("_files") or []:
            name = item.get("name")
            data = item.get("bytes")
            if not name or not data or name in seen:
                continue
            if not str(name).startswith("selfies/"):
                continue
            seen.add(name)
            zf.writestr(name, data)
    return buf.getvalue()


async def _collect(cursor, limit: int) -> list[dict]:
    if cursor is None:
        return []
    limited = cursor
    if hasattr(cursor, "limit"):
        try:
            limited = cursor.limit(limit)
        except Exception:
            limited = cursor
    if hasattr(limited, "to_list"):
        docs = await limited.to_list(length=limit)
        return list(docs or [])
    return []


async def load_evidence_manifest(booking_number: str, *, limit: int = DEFAULT_LIMIT) -> Optional[dict]:
    booking_number = (booking_number or "").strip()
    if not booking_number:
        return None
    n = clamp_limit(limit)
    bonds = get_collection("active_bonds")
    logs = get_collection("check_in_log")
    checkins = get_collection("bond_checkins")
    bond = await bonds.find_one({"booking_number": booking_number})
    fetch_cap = max(n, 200)
    log_rows = await _collect(
        logs.find({"booking_number": booking_number}),
        fetch_cap,
    )
    portal_rows = await _collect(
        checkins.find({"booking_number": booking_number}),
        fetch_cap,
    )
    if bond is None and not log_rows and not portal_rows:
        return None
    if bond:
        bond = dict(bond)
        bond.pop("_id", None)
        bond.pop("defendant_phone", None)
        bond.pop("phone", None)
    return build_evidence_manifest(
        booking_number=booking_number,
        bond=bond,
        check_in_logs=log_rows,
        bond_checkins=portal_rows,
        limit=n,
    )


def _signed_booking(doc: dict) -> str:
    return str(doc.get("booking_number") or doc.get("Booking_Number") or "").strip()


async def list_signed_bonds_checkin_not_sent(*, limit: int = 100) -> dict:
    """
    Signed bonds whose check-in link has not been sent.

    A row qualifies when paperwork_packets or bond_cases records a signed
    packet and active_bonds.checkin_link_last_sent_at is empty. Pending
    checkin_enroll tasks are attached when they exist. Unsigned bonds are
    omitted even if a task is open.
    """
    cap = max(1, min(int(limit or 100), 200))
    packets = await _collect(
        get_collection("paperwork_packets").find(
            {"status": {"$in": list(_SIGNED_PACKET_STATUSES)}},
            {"booking_number": 1, "signed_at": 1, "defendant_name": 1, "packet_id": 1, "status": 1},
        ),
        500,
    )
    cases = await _collect(
        get_collection("bond_cases").find(
            {"$or": [{"Signature_Status": "signed"}, {"Packet_Status": "signed"}]},
            {
                "booking_number": 1,
                "defendant_name": 1,
                "Defendant_Name": 1,
                "signed_at": 1,
                "Signature_Status": 1,
                "Packet_Status": 1,
            },
        ),
        500,
    )
    tasks = await _collect(
        get_collection("tasks").find(
            {"task_type": "checkin_enroll", "status": "pending"},
            {"booking_number": 1, "title": 1, "due_date": 1, "created_at": 1},
        ),
        500,
    )
    task_by_booking: dict[str, dict] = {}
    for task in tasks:
        bk = _signed_booking(task)
        if bk and bk not in task_by_booking:
            task_by_booking[bk] = task

    reasons: dict[str, set[str]] = {}
    names: dict[str, str] = {}
    signed_at: dict[str, Optional[str]] = {}

    def _note(doc: dict, reason: str) -> None:
        bk = _signed_booking(doc)
        if not bk:
            return
        reasons.setdefault(bk, set()).add(reason)
        name = doc.get("defendant_name") or doc.get("Defendant_Name") or ""
        if name and not names.get(bk):
            names[bk] = str(name)
        stamp = _iso(doc.get("signed_at"))
        if stamp and not signed_at.get(bk):
            signed_at[bk] = stamp

    for doc in packets:
        _note(doc, "signed_packet")
    for doc in cases:
        _note(doc, "signed_bond_case")

    items = []
    bonds = get_collection("active_bonds")
    for booking_number in sorted(reasons):
        bond = await bonds.find_one(
            {"booking_number": booking_number},
            {
                "defendant_name": 1,
                "county": 1,
                "status": 1,
                "check_in_required": 1,
                "checkin_link_last_sent_at": 1,
                "checkin_enabled_at": 1,
                "next_check_in_due": 1,
                "next_checkin_due": 1,
            },
        )
        sent = _iso((bond or {}).get("checkin_link_last_sent_at"))
        if sent:
            continue
        task = task_by_booking.get(booking_number)
        if task:
            reasons[booking_number].add("pending_enroll_task")
        items.append({
            "booking_number": booking_number,
            "defendant_name": (bond or {}).get("defendant_name") or names.get(booking_number) or "",
            "county": (bond or {}).get("county") or "",
            "bond_status": (bond or {}).get("status") or "",
            "signed_at": signed_at.get(booking_number),
            "check_in_required": bool((bond or {}).get("check_in_required")),
            "checkin_link_last_sent_at": None,
            "checkin_enabled_at": _iso((bond or {}).get("checkin_enabled_at")),
            "next_due": _iso((bond or {}).get("next_check_in_due") or (bond or {}).get("next_checkin_due")),
            "reasons": sorted(reasons[booking_number]),
            "enroll_task_due": _iso((task or {}).get("due_date")),
            "label": SLA_LABEL,
        })
        if len(items) >= cap:
            break

    return {
        "success": True,
        "label": SLA_LABEL,
        "count": len(items),
        "items": items,
    }


async def audit_evidence_download(*, booking_number: str, actor: str, entry_count: int, empty: bool) -> None:
    try:
        await get_collection("audit_events").insert_one({
            "event_type": "checkin_evidence_downloaded",
            "entity_type": "bond_case",
            "entity_id": booking_number,
            "timestamp": datetime.now(timezone.utc),
            "actor": actor or "staff",
            "source": "checkin_evidence_pack",
            "details": {
                "entry_count": entry_count,
                "empty": empty,
            },
        })
    except Exception as exc:
        logger.warning("check-in evidence audit failed booking=%s err=%s", booking_number, type(exc).__name__)
