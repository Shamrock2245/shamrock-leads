"""Staff-confirmed Lee County booking URL intake.

The service is intentionally narrow: it accepts a specific public Lee booking URL,
projects only operational booking facts, and creates/refreshes one ArrestLead after
a staff confirmation. It is not a person-enrichment or contact-discovery service.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import parse_qs, urlsplit

LEE_BOOKING_HOSTS = frozenset({"sheriffleefl.org", "www.sheriffleefl.org"})
PREVIEW_TTL_SECONDS = 15 * 60
PROTECTED_STATUSES = frozenset({"active", "bonded", "written", "signed", "closed"})


class BookingIntakeError(ValueError):
    """A safe client-facing booking-intake rejection."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _money(value: Any) -> float:
    try:
        cleaned = str(value or "").replace("$", "").replace(",", "").strip()
        return round(float(cleaned), 2) if cleaned else 0.0
    except (TypeError, ValueError):
        return 0.0


def normalize_lee_booking_url(raw_url: str) -> tuple[str, str]:
    """Return a canonical public booking URL and numeric booking ID.

    Only the official Lee host and a single positive numeric booking id are allowed.
    This prevents the endpoint from becoming a generic server-side URL fetcher.
    """
    value = (raw_url or "").strip()
    if not value:
        raise BookingIntakeError("A Lee County booking URL is required.", 400)
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise BookingIntakeError("The booking URL is malformed.", 400) from exc

    if parsed.scheme.lower() != "https":
        raise BookingIntakeError("Only HTTPS Lee County booking URLs are supported.", 400)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in LEE_BOOKING_HOSTS:
        raise BookingIntakeError("Only the official Lee County booking host is supported.", 400)
    if parsed.username or parsed.password or parsed.port:
        raise BookingIntakeError("The booking URL contains an unsupported authority component.", 400)

    path = (parsed.path or "/").rstrip("/") or "/"
    booking_id = ""
    query = parse_qs(parsed.query, keep_blank_values=True)
    if path == "/booking":
        ids = query.get("id") or []
        if len(ids) != 1:
            raise BookingIntakeError("Provide exactly one Lee County booking id.", 400)
        booking_id = ids[0].strip()
    else:
        parts = [part for part in path.split("/") if part]
        if len(parts) == 3 and parts[0] == "public-api" and parts[1] == "bookings" and not query:
            booking_id = parts[2]
        else:
            raise BookingIntakeError("The URL is not a supported Lee County booking page.", 400)

    if not booking_id.isdigit() or int(booking_id) <= 0:
        raise BookingIntakeError("The Lee County booking id must contain positive digits only.", 400)
    return f"https://www.sheriffleefl.org/booking/?id={booking_id}", booking_id


def _safe_charge_rows(raw_rows: Any, fallback: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(raw_rows, list):
        for raw in raw_rows[:30]:
            if not isinstance(raw, dict):
                continue
            charge = _clean_text(raw.get("charge") or raw.get("description"), 240)
            if not charge:
                continue
            rows.append({
                "charge": charge,
                "bond_amount": _money(raw.get("bond_amount") or raw.get("bondAmount")),
                "bond_type": _clean_text(raw.get("bond_type") or raw.get("bondType"), 80),
                "case_number": _clean_text(raw.get("case_number") or raw.get("caseNumber"), 80),
                "court_date": _clean_text(raw.get("court_date") or raw.get("courtDate"), 40),
                "court_time": _clean_text(raw.get("court_time") or raw.get("courtTime"), 40),
                "court_location": _clean_text(raw.get("court_location") or raw.get("courtLocation"), 120),
            })
    if rows:
        return rows
    return [{"charge": text, "bond_amount": 0.0, "bond_type": "", "case_number": "", "court_date": "", "court_time": "", "court_location": ""}
            for text in [_clean_text(item, 240) for item in str(fallback or "").split("|")] if text]


def project_public_booking_facts(parsed_data: dict[str, Any], *, booking_id: str, source_url: str, parse_method: str) -> dict[str, Any]:
    """Explicitly project a parser result into the approved booking-fact schema."""
    name = _clean_text(parsed_data.get("full_name"), 160)
    parsed_booking = _clean_text(parsed_data.get("booking_number"), 80)
    parsed_county = _clean_text(parsed_data.get("county"), 80)
    if not name:
        raise BookingIntakeError("The official source did not return an exact defendant name; no preview was created.")
    if parsed_booking != booking_id:
        raise BookingIntakeError("The official source returned a booking number that does not match the pasted URL.")
    if parsed_county.lower() != "lee":
        raise BookingIntakeError("The official source returned a county that does not match the Lee booking URL.")

    charge_rows = _safe_charge_rows(parsed_data.get("charge_details"), parsed_data.get("charges"))
    charge_text = " | ".join(row["charge"] for row in charge_rows if row.get("charge"))
    total_bond = _money(parsed_data.get("bond_amount"))
    if total_bond <= 0:
        total_bond = round(sum(_money(row.get("bond_amount")) for row in charge_rows), 2)

    return {
        "booking_number": booking_id,
        "county": "Lee",
        "state": "FL",
        "full_name": name,
        "facility": _clean_text(parsed_data.get("facility") or "Lee County Jail", 120),
        "custody_status": _clean_text(parsed_data.get("status") or parsed_data.get("custody_status"), 80),
        "booking_date": _clean_text(parsed_data.get("booking_date") or parsed_data.get("arrest_date"), 40),
        "charges": charge_text,
        "charge_details": charge_rows,
        "bond_amount": total_bond,
        "bond_type": _clean_text(parsed_data.get("bond_type"), 80),
        "case_number": _clean_text(parsed_data.get("case_number"), 120),
        "court_date": _clean_text(parsed_data.get("court_date"), 40),
        "court_time": _clean_text(parsed_data.get("court_time"), 40),
        "court_location": _clean_text(parsed_data.get("court_location"), 120),
        "source_url": source_url,
        "source_host": "www.sheriffleefl.org",
        "parse_method": _clean_text(parse_method or "lee_county_api", 80),
        "booking_dedup_key": f"FL|LEE|{booking_id}",
    }


async def build_preview(raw_url: str, preview_collection: Any) -> dict[str, Any]:
    """Fetch a supported booking, minimize it, and store a short-lived preview."""
    source_url, booking_id = normalize_lee_booking_url(raw_url)
    from dashboard.services.url_ingest_service import ingest_url

    result = await ingest_url(source_url)
    if not result.get("success") or not isinstance(result.get("data"), dict):
        raise BookingIntakeError("The official booking source could not provide a confirmable record.")
    facts = project_public_booking_facts(
        result["data"],
        booking_id=booking_id,
        source_url=source_url,
        parse_method=str(result.get("parse_method") or "lee_county_api"),
    )

    now = _now()
    preview_id = f"bip_{secrets.token_urlsafe(18)}"
    doc = {
        "preview_id": preview_id,
        "facts": facts,
        "created_at": now,
        "expires_at": now + timedelta(seconds=PREVIEW_TTL_SECONDS),
        "consumed_at": None,
    }
    try:
        await preview_collection.create_index(
            [("expires_at", 1)],
            expireAfterSeconds=0,
            name="ttl_confirmed_booking_preview",
        )
    except Exception:
        # Cleanup is still enforced at read/confirm time. Index availability is an ops concern.
        pass
    await preview_collection.insert_one(doc)
    return {
        "preview_id": preview_id,
        "expires_at": doc["expires_at"].isoformat(),
        "preview": facts,
        "data_mode": "public_booking_minimized",
        "warnings": [
            "Preview contains only permitted published booking facts.",
            "No address, contact, relative, household, DOB, or external enrichment data is included.",
            "Staff must confirm the exact booking number before an arrest lead can be created or refreshed.",
        ],
    }


def _row_county_state(row: dict[str, Any]) -> tuple[str, str]:
    county = _clean_text(row.get("county") or row.get("County"), 80).upper()
    state = _clean_text(row.get("state") or row.get("State") or "FL", 10).upper()
    return county, state


def _protected_arrest(row: dict[str, Any]) -> bool:
    status = _clean_text(row.get("status") or row.get("bond_status"), 40).lower()
    return bool(
        row.get("bond_written")
        or row.get("paperwork_packet_id")
        or row.get("packet_id")
        or row.get("signed_at")
        or status in PROTECTED_STATUSES
    )


async def _rows_for_booking(arrests: Any, booking_number: str) -> list[dict[str, Any]]:
    cursor = arrests.find({"$or": [{"booking_number": booking_number}, {"Booking_Number": booking_number}]})
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=20)
    rows: list[dict[str, Any]] = []
    async for row in cursor:
        rows.append(row)
    return rows


async def _ensure_booking_dedup_index(arrests: Any) -> None:
    """Require a canonical compound key before a staff-confirmed upsert."""
    try:
        await arrests.create_index(
            [("booking_dedup_key", 1)],
            unique=True,
            sparse=True,
            name="idx_booking_dedup_key_unique",
        )
    except Exception as exc:
        raise BookingIntakeError(
            "The canonical booking deduplication guard is unavailable. No record was changed.",
            503,
        ) from exc


async def confirm_preview(
    *,
    preview_id: str,
    confirmed_booking_number: str,
    exact_match_confirmed: bool,
    preview_collection: Any,
    arrests_collection: Any,
    audit_collection: Any,
) -> dict[str, Any]:
    """Consume one preview and idempotently create or refresh an unprotected arrest lead."""
    if not exact_match_confirmed:
        raise BookingIntakeError("Staff acknowledgement of the exact booking is required.", 409)
    supplied_booking = _clean_text(confirmed_booking_number, 80)
    if not supplied_booking:
        raise BookingIntakeError("Re-enter the booking number shown in the preview.", 422)

    now = _now()
    preview_doc = await preview_collection.find_one({
        "preview_id": _clean_text(preview_id, 160),
        "consumed_at": None,
        "expires_at": {"$gt": now},
    })
    if not preview_doc:
        raise BookingIntakeError("The booking preview is missing, expired, or already confirmed. Create a new preview.", 409)
    facts = preview_doc.get("facts") or {}
    if supplied_booking != facts.get("booking_number"):
        raise BookingIntakeError("The re-entered booking number does not match the preview. No record was changed.", 409)

    await _ensure_booking_dedup_index(arrests_collection)
    rows = await _rows_for_booking(arrests_collection, supplied_booking)
    same_jurisdiction: dict[str, Any] | None = None
    for row in rows:
        county, state = _row_county_state(row)
        if county == "LEE" and state == "FL":
            same_jurisdiction = row
        else:
            return {
                "success": False,
                "outcome": "requires_staff_review",
                "message": "A record with this booking number exists under a different county or state. No record was changed.",
                "booking_dedup_key": facts["booking_dedup_key"],
            }

    if same_jurisdiction and _protected_arrest(same_jurisdiction):
        return {
            "success": False,
            "outcome": "requires_staff_review",
            "message": "The matching arrest lead is protected by a downstream workflow. No record was changed.",
            "booking_dedup_key": facts["booking_dedup_key"],
        }

    safe_fields = {
        key: facts[key]
        for key in (
            "booking_number", "county", "state", "full_name", "facility", "custody_status",
            "booking_date", "charges", "charge_details", "bond_amount", "bond_type", "case_number",
            "court_date", "court_time", "court_location", "source_url", "booking_dedup_key",
        )
    }
    safe_fields.update({
        "ingestion_method": "confirmed_booking_url",
        "booking_source": {
            "host": facts["source_host"],
            "parser": facts["parse_method"],
            "confirmed_at": now,
            "preview_id": preview_doc["preview_id"],
        },
        "scraped_at": now,
        "updated_at": now,
    })
    created = same_jurisdiction is None
    filter_doc = {"booking_dedup_key": facts["booking_dedup_key"]} if created else {"_id": same_jurisdiction["_id"]}
    await arrests_collection.update_one(
        filter_doc,
        {"$set": safe_fields, "$setOnInsert": {"created_at": now}},
        upsert=created,
    )

    outcome = "created" if created else "refreshed"
    await preview_collection.update_one(
        {"_id": preview_doc["_id"], "consumed_at": None},
        {"$set": {"consumed_at": now, "consumed_outcome": outcome}},
    )
    try:
        await audit_collection.insert_one({
            "entity_id": facts["booking_dedup_key"],
            "event_type": f"booking_url_intake_{outcome}",
            "timestamp": now,
            "source_host": facts["source_host"],
            "parser": facts["parse_method"],
        })
    except Exception:
        # The confirmed lead remains valid; audit store availability must not duplicate a write.
        pass

    return {
        "success": True,
        "outcome": outcome,
        "booking_dedup_key": facts["booking_dedup_key"],
        "message": "Confirmed booking created as an arrest lead." if created else "Confirmed booking refreshed on the existing arrest lead.",
    }
