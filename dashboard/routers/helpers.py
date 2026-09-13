"""Shared helpers for FastAPI routers."""
from __future__ import annotations

import csv
import io
import logging
import math
from datetime import datetime
from bson import ObjectId
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in miles between two GPS coordinates using the Haversine formula."""
    R = 3958.8  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def mask_phone(phone: str | None) -> str:
    """PII-safe phone representation for logs — keep only the last 4 digits.

    SOC II logging rule: never write a full phone number to application logs.
    """
    if not phone:
        return "(none)"
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    return f"...{digits[-4:]}" if len(digits) >= 4 else "...****"


def serialize_doc(doc: dict) -> dict:
    """Convert values to JSON-safe types.

    - datetime  → ISO-8601 string
    - ObjectId  → hex string
    - booking_number (int/float) → string  (prevents JS .replace() TypeError)
    """
    for k, v in list(doc.items()):
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
        elif isinstance(v, ObjectId):
            doc[k] = str(v)
        elif k == "booking_number" and not isinstance(v, str):
            doc[k] = str(v) if v is not None else ""
    return attach_write_eligible(doc)


def attach_write_eligible(doc: dict) -> dict:
    """Set write_eligible when county is present and the flag is missing."""
    if not isinstance(doc, dict):
        return doc
    if "county" in doc and "write_eligible" not in doc:
        from config.write_counties import is_write_eligible
        doc["write_eligible"] = is_write_eligible(doc.get("county"), doc.get("state"))
    return doc


def _truthy_override(value) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return value in (1,)


async def reject_unless_write_book(
    *,
    county: str,
    state: str | None = None,
    body: dict | None = None,
    action: str,
    entity_id: str = "",
    actor: str = "dashboard",
) -> JSONResponse | None:
    """Return a 403/400 response when the county is off the write book.

    Staff may proceed with write_book_override=true and a reason of 8+ chars.
    Override is written to audit_events.
    """
    from config.write_counties import evaluate_write_book

    body = body or {}
    override = _truthy_override(body.get("write_book_override"))
    reason = str(body.get("write_book_override_reason") or "").strip()
    result = evaluate_write_book(
        county, state, override=override, override_reason=reason,
    )
    if result["allowed"]:
        if result["override_applied"]:
            try:
                from dashboard.services.audit_service import AuditService
                await AuditService.log_event(
                    entity_type="bond_case",
                    entity_id=str(entity_id or county or "unknown"),
                    action="write_book_override",
                    details={
                        "county": county or "",
                        "state": state or "",
                        "reason": reason,
                        "gate": action,
                    },
                    actor=actor or "dashboard",
                    actor_type="staff",
                    event_context=action,
                )
            except Exception as exc:
                logger.warning("write_book_override audit failed: %s", exc)
        return None

    if result["error"] == "not_write_eligible":
        label = county or "(missing county)"
        return JSONResponse(
            {
                "success": False,
                "error": "not_write_eligible",
                "message": (
                    f"{label} is not on the Shamrock Florida write book. "
                    "Pass write_book_override=true and write_book_override_reason "
                    "(8+ characters) to proceed."
                ),
                "county": county or "",
                "state": state or "",
                "write_eligible": False,
            },
            status_code=403,
        )
    return JSONResponse(
        {
            "success": False,
            "error": result["error"],
            "write_eligible": False,
        },
        status_code=400,
    )


async def async_csv_streamer(cursor, fieldnames: list[str]):
    """Asynchronous generator to stream CSV rows using DictWriter.

    Enforces memory safety by flushing each row directly and prevents crashes
    from MongoDB schema variations using extrasaction='ignore'.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")

    # Write and yield the header row
    writer.writeheader()
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    # Stream documents from MongoDB cursor
    async for doc in cursor:
        clean_doc = {}
        for k in fieldnames:
            v = doc.get(k)
            if v is None:
                clean_doc[k] = ""
            elif isinstance(v, datetime):
                clean_doc[k] = v.isoformat()
            elif isinstance(v, ObjectId):
                clean_doc[k] = str(v)
            elif isinstance(v, (list, dict)):
                clean_doc[k] = str(v)
            else:
                clean_doc[k] = v

        writer.writerow(clean_doc)
        data = buffer.getvalue()
        yield data
        buffer.seek(0)
        buffer.truncate(0)


async def async_csv_list_streamer(cursor, row_extractor_fn, header: list[str]):
    """Asynchronous generator to stream CSV rows generated as list rows.

    Takes a motor cursor, a mapping function `row_extractor_fn(doc) -> list`,
    and a header list, streaming formatted rows securely.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    # Write and yield the header row
    writer.writerow(header)
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    # Stream and extract rows
    async for doc in cursor:
        row = row_extractor_fn(doc)
        clean_row = []
        for v in row:
            if v is None:
                clean_row.append("")
            elif isinstance(v, datetime):
                clean_row.append(v.isoformat())
            elif isinstance(v, ObjectId):
                clean_row.append(str(v))
            elif isinstance(v, (list, dict)):
                clean_row.append(str(v))
            else:
                clean_row.append(v)

        writer.writerow(clean_row)
        data = buffer.getvalue()
        yield data
        buffer.seek(0)
        buffer.truncate(0)
