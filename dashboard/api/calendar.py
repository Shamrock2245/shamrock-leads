"""ShamrockLeads — Court Calendar API Blueprint

Endpoints:
  GET /api/calendar/events     — Court dates as calendar events
  GET /api/calendar/reminders  — Scheduled reminder status per event
  GET /api/calendar/upcoming   — Upcoming court dates (next N days)

All routes use Quart (async) + Motor (async MongoDB).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from quart import Blueprint, jsonify, request

from dashboard.extensions import get_db

logger = logging.getLogger(__name__)

calendar_bp = Blueprint("calendar", __name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _urgency(court_date: datetime | str | None) -> str:
    """Return urgency level: today | this_week | upcoming | overdue | unknown."""
    if not court_date:
        return "unknown"
    try:
        if isinstance(court_date, str):
            court_date = datetime.fromisoformat(court_date.replace("Z", "+00:00"))
        if court_date.tzinfo is None:
            court_date = court_date.replace(tzinfo=timezone.utc)
        now = _utc_now()
        diff = (court_date.date() - now.date()).days
        if diff < 0:
            return "overdue"
        if diff == 0:
            return "today"
        if diff <= 7:
            return "this_week"
        return "upcoming"
    except Exception:
        return "unknown"


@calendar_bp.route("/calendar/events")
async def calendar_events():
    """
    Returns court dates from active_bonds as calendar event objects.
    Query params:
      start  — ISO date string (default: today)
      end    — ISO date string (default: 90 days from today)
      county — filter by county (optional)
    """
    try:
        db = get_db()
        active_bonds_col = db["active_bonds"]
        court_reminders_col = db["court_reminders"]

        start_str = request.args.get("start")
        end_str = request.args.get("end")
        county = request.args.get("county", "")

        now = _utc_now()
        start = datetime.fromisoformat(start_str) if start_str else now - timedelta(days=7)
        end = datetime.fromisoformat(end_str) if end_str else now + timedelta(days=90)

        # Ensure timezone aware
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        match: dict = {
            "court_date": {"$gte": start, "$lte": end}
        }
        if county:
            match["county"] = county

        bonds = await active_bonds_col.find(match, {
            "booking_number": 1, "defendant_name": 1, "county": 1,
            "court_date": 1, "bond_amount": 1, "case_number": 1,
            "status": 1, "risk_score": 1, "insurance_company": 1,
            "indemnitor_name": 1, "indemnitor_phone": 1
        }).sort("court_date", 1).to_list(None)

        # Fetch reminder statuses in bulk
        booking_numbers = [b.get("booking_number") for b in bonds if b.get("booking_number")]
        reminders_map: dict = {}
        if booking_numbers:
            reminders = await court_reminders_col.find(
                {"booking_number": {"$in": booking_numbers}}
            ).to_list(None)
            for r in reminders:
                bn = r.get("booking_number")
                if bn:
                    reminders_map[bn] = r

        events = []
        for bond in bonds:
            cd = bond.get("court_date")
            urgency = _urgency(cd)
            color_map = {
                "today": "#ef4444",
                "this_week": "#f97316",
                "upcoming": "#3b82f6",
                "overdue": "#7c3aed",
                "unknown": "#6b7280"
            }
            bn = bond.get("booking_number", "")
            reminder = reminders_map.get(bn, {})

            events.append({
                "id": str(bond.get("_id", "")),
                "booking_number": bn,
                "title": bond.get("defendant_name", "Unknown"),
                "county": bond.get("county", ""),
                "court_date": cd.isoformat() if isinstance(cd, datetime) else str(cd or ""),
                "bond_amount": bond.get("bond_amount", 0),
                "case_number": bond.get("case_number", ""),
                "status": bond.get("status", ""),
                "risk_score": bond.get("risk_score", 0),
                "surety": bond.get("insurance_company", ""),
                "indemnitor_name": bond.get("indemnitor_name", ""),
                "indemnitor_phone": bond.get("indemnitor_phone", ""),
                "urgency": urgency,
                "color": color_map.get(urgency, "#6b7280"),
                "reminder_status": reminder.get("status", "none"),
                "reminders_sent": reminder.get("reminders_sent", 0),
                "last_reminder_at": reminder.get("last_reminder_at", None),
            })

        # Summary counts
        summary = {
            "today": sum(1 for e in events if e["urgency"] == "today"),
            "this_week": sum(1 for e in events if e["urgency"] == "this_week"),
            "upcoming": sum(1 for e in events if e["urgency"] == "upcoming"),
            "overdue": sum(1 for e in events if e["urgency"] == "overdue"),
            "total": len(events)
        }

        return jsonify({"success": True, "events": events, "summary": summary})
    except Exception as exc:
        logger.exception("calendar/events error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


@calendar_bp.route("/calendar/reminders")
async def calendar_reminders():
    """Returns scheduled reminder status for all active bonds with court dates."""
    try:
        db = get_db()
        court_reminders_col = db["court_reminders"]

        reminders = await court_reminders_col.find({}, {
            "booking_number": 1, "defendant_name": 1, "county": 1,
            "court_date": 1, "status": 1, "reminders_sent": 1,
            "last_reminder_at": 1, "next_reminder_at": 1, "created_at": 1
        }).sort("court_date", 1).to_list(None)

        result = []
        for r in reminders:
            cd = r.get("court_date")
            result.append({
                "booking_number": r.get("booking_number", ""),
                "defendant_name": r.get("defendant_name", ""),
                "county": r.get("county", ""),
                "court_date": cd.isoformat() if isinstance(cd, datetime) else str(cd or ""),
                "status": r.get("status", ""),
                "reminders_sent": r.get("reminders_sent", 0),
                "last_reminder_at": r.get("last_reminder_at", ""),
                "next_reminder_at": r.get("next_reminder_at", ""),
            })

        return jsonify({"success": True, "reminders": result, "total": len(result)})
    except Exception as exc:
        logger.exception("calendar/reminders error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


@calendar_bp.route("/calendar/upcoming")
async def upcoming_events():
    """Returns the next N court dates (default 14 days)."""
    try:
        db = get_db()
        days = int(request.args.get("days", 14))
        active_bonds_col = db["active_bonds"]

        now = _utc_now()
        end = now + timedelta(days=days)

        bonds = await active_bonds_col.find(
            {"court_date": {"$gte": now, "$lte": end}},
            {"booking_number": 1, "defendant_name": 1, "county": 1,
             "court_date": 1, "bond_amount": 1, "status": 1}
        ).sort("court_date", 1).to_list(50)

        events = []
        for bond in bonds:
            cd = bond.get("court_date")
            events.append({
                "booking_number": bond.get("booking_number", ""),
                "defendant_name": bond.get("defendant_name", ""),
                "county": bond.get("county", ""),
                "court_date": cd.isoformat() if isinstance(cd, datetime) else str(cd or ""),
                "bond_amount": bond.get("bond_amount", 0),
                "urgency": _urgency(cd),
            })

        return jsonify({"success": True, "events": events, "days": days})
    except Exception as exc:
        logger.exception("calendar/upcoming error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# Feature K: Google Calendar Sync
# Push court dates → shared Google Calendar (dedup via extendedProperties)
# ═══════════════════════════════════════════════════════════════════════════════

@calendar_bp.route("/calendar/sync-gcal", methods=["POST"])
async def sync_to_gcal():
    """Push a single bond's court date to Google Calendar.

    Body: { "booking_number": "2025-001234" }

    Creates a Google Calendar event with:
      - Title: "🏛 Court: {defendant} — {county} County"
      - Date/time from bond's court_date + court_time
      - Description with case details
      - Dedup key: booking_number-court_date in extendedProperties

    Requires GOOGLE_CALENDAR_ID and GOOGLE_CREDENTIALS_JSON env vars.
    """
    import os
    data = await request.get_json(force=True) or {}
    booking_number = data.get("booking_number", "").strip()
    if not booking_number:
        return jsonify({"error": "booking_number required"}), 400

    calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "")
    creds_path = os.getenv("GOOGLE_CREDENTIALS_JSON", "")

    if not calendar_id or not creds_path:
        return jsonify({
            "success": False,
            "error": "Google Calendar not configured",
            "setup": [
                "1. Create a Google Calendar 'Shamrock Court Dates'",
                "2. Share it with your service account email",
                "3. Set GOOGLE_CALENDAR_ID=<calendar_id> in .env",
                "4. Set GOOGLE_CREDENTIALS_JSON=/path/to/key.json in .env",
            ],
        }), 501

    db = get_db()
    bond = await db["active_bonds"].find_one(
        {"booking_number": booking_number},
        {"_id": 0, "defendant_name": 1, "county": 1, "court_date": 1,
         "court_time": 1, "court_location": 1, "case_number": 1,
         "bond_amount": 1, "insurance_company": 1, "poa_number": 1},
    )
    if not bond:
        return jsonify({"error": f"Bond {booking_number} not found"}), 404
    if not bond.get("court_date"):
        return jsonify({"error": f"Bond {booking_number} has no court date"}), 400

    # Build dedup key
    court_date_str = str(bond["court_date"])
    dedup_key = f"{booking_number}-{court_date_str[:10]}"

    # Check if already synced
    gcal_sync = db["gcal_sync"]
    existing = await gcal_sync.find_one({"dedup_key": dedup_key})
    if existing:
        return jsonify({
            "success": True,
            "already_synced": True,
            "gcal_event_id": existing.get("gcal_event_id", ""),
            "dedup_key": dedup_key,
        })

    # Build event payload
    defendant = bond.get("defendant_name", "Unknown")
    county = bond.get("county", "")
    court_location = bond.get("court_location", f"{county} County Courthouse")
    case_number = bond.get("case_number", "N/A")
    bond_amount = bond.get("bond_amount", 0)
    surety = bond.get("insurance_company", "")
    poa = bond.get("poa_number", "")

    # Parse court date
    if isinstance(bond["court_date"], datetime):
        event_date = bond["court_date"]
    else:
        try:
            event_date = datetime.fromisoformat(str(bond["court_date"]).replace("Z", "+00:00"))
        except Exception:
            event_date = datetime.now(timezone.utc)

    court_time = bond.get("court_time", "8:30 AM")

    event_body = {
        "summary": f"🏛 Court: {defendant} — {county} County",
        "location": court_location,
        "description": (
            f"Defendant: {defendant}\n"
            f"Booking #: {booking_number}\n"
            f"Case #: {case_number}\n"
            f"Bond: ${bond_amount:,.2f} ({surety})\n"
            f"POA: {poa}\n"
            f"Court Time: {court_time}\n\n"
            f"— Shamrock Bail Bonds"
        ),
        "start": {
            "dateTime": event_date.strftime("%Y-%m-%dT08:30:00"),
            "timeZone": "America/New_York",
        },
        "end": {
            "dateTime": event_date.strftime("%Y-%m-%dT09:30:00"),
            "timeZone": "America/New_York",
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 1440},   # 1 day before
                {"method": "popup", "minutes": 60},      # 1 hour before
            ],
        },
        "extendedProperties": {
            "private": {
                "shamrock_booking": booking_number,
                "shamrock_dedup": dedup_key,
            },
        },
    }

    # Store the sync record (actual GCal API call will be wired when creds are configured)
    await gcal_sync.update_one(
        {"dedup_key": dedup_key},
        {"$set": {
            "dedup_key": dedup_key,
            "booking_number": booking_number,
            "defendant_name": defendant,
            "county": county,
            "court_date": court_date_str,
            "event_payload": event_body,
            "status": "queued",
            "created_at": _utc_now().isoformat(),
        }},
        upsert=True,
    )

    logger.info("[gcal] Queued event for %s — %s (%s)", booking_number, defendant, county)

    return jsonify({
        "success": True,
        "dedup_key": dedup_key,
        "event": {
            "summary": event_body["summary"],
            "date": event_date.strftime("%Y-%m-%d"),
            "location": court_location,
        },
        "note": "Event queued. Will sync when Google Calendar API credentials are configured.",
    })


@calendar_bp.route("/calendar/sync-all", methods=["POST"])
async def sync_all_to_gcal():
    """Batch sync: push ALL active bonds with court dates to Google Calendar."""
    db = get_db()
    now = _utc_now()

    bonds = await db["active_bonds"].find(
        {
            "court_date": {"$gte": now.isoformat()},
            "status": {"$in": ["active", "monitoring"]},
        },
        {"booking_number": 1, "defendant_name": 1, "county": 1, "court_date": 1},
    ).to_list(200)

    gcal_sync = db["gcal_sync"]
    queued = 0
    skipped = 0

    for bond in bonds:
        booking = bond.get("booking_number", "")
        court_date_str = str(bond.get("court_date", ""))[:10]
        dedup_key = f"{booking}-{court_date_str}"

        existing = await gcal_sync.find_one({"dedup_key": dedup_key})
        if existing:
            skipped += 1
            continue

        await gcal_sync.update_one(
            {"dedup_key": dedup_key},
            {"$set": {
                "dedup_key": dedup_key,
                "booking_number": booking,
                "defendant_name": bond.get("defendant_name", ""),
                "county": bond.get("county", ""),
                "court_date": court_date_str,
                "status": "queued",
                "created_at": now.isoformat(),
            }},
            upsert=True,
        )
        queued += 1

    return jsonify({
        "success": True,
        "bonds_found": len(bonds),
        "queued": queued,
        "skipped": skipped,
    })

