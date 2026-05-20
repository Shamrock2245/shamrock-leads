from __future__ import annotations

"""ShamrockLeads — Court Calendar API Blueprint

Endpoints:
  GET  /api/calendar/events       — Court dates as calendar event objects
  GET  /api/calendar/reminders    — Scheduled reminder status per event
  GET  /api/calendar/upcoming     — Upcoming court dates (next N days)
  POST /api/calendar/sync-gcal    — Sync court dates to Google Calendar (Feature K)

All routes use Quart (async) + Motor (async MongoDB).
"""

import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from dashboard.extensions import get_db, get_collection

logger = logging.getLogger(__name__)

calendar_bp = APIRouter(prefix="/api", tags=["calendar"])
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


@calendar_bp.get("/calendar/events")
async def calendar_events(start: str | None = Query(default=None), end: str | None = Query(default=None), county: str = Query(default="")):
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

        start_str = start
        end_str = end
        county = county

        now = _utc_now()
        start = datetime.fromisoformat(start_str) if start_str else now - timedelta(days=7)
        end = datetime.fromisoformat(end_str) if end_str else now + timedelta(days=90)

        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        match: dict = {"court_date": {"$gte": start, "$lte": end}}
        if county:
            match["county"] = county

        bonds = await active_bonds_col.find(match, {
            "booking_number": 1, "defendant_name": 1, "county": 1,
            "court_date": 1, "bond_amount": 1, "case_number": 1,
            "status": 1, "risk_score": 1, "insurance_company": 1,
            "indemnitor_name": 1, "indemnitor_phone": 1
        }).sort("court_date", 1).to_list(None)

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

        summary = {
            "today": sum(1 for e in events if e["urgency"] == "today"),
            "this_week": sum(1 for e in events if e["urgency"] == "this_week"),
            "upcoming": sum(1 for e in events if e["urgency"] == "upcoming"),
            "overdue": sum(1 for e in events if e["urgency"] == "overdue"),
            "total": len(events)
        }

        return {"success": True, "events": events, "summary": summary}
    except Exception as exc:
        logger.exception("calendar/events error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@calendar_bp.get("/calendar/reminders")
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

        return {"success": True, "reminders": result, "total": len(result)}
    except Exception as exc:
        logger.exception("calendar/reminders error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@calendar_bp.get("/calendar/upcoming")
async def upcoming_events(days: int = Query(default=14)):
    """Returns the next N court dates (default 14 days)."""
    try:
        db = get_db()
        days = int(days)
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

        return {"success": True, "events": events, "days": days}
    except Exception as exc:
        logger.exception("calendar/upcoming error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ── Google Calendar Sync (Feature K) ─────────────────────────────────────────

@calendar_bp.post("/calendar/sync-gcal")
async def calendar_sync_gcal(request: Request):
    """
    Sync upcoming court dates to Google Calendar (admin@shamrockbailbonds.biz).

    Returns 501 when GOOGLE_APPLICATION_CREDENTIALS is not set.
    See docs/GCAL_SYNC_SETUP.md for the full setup guide.

    Body (all optional):
        {
            "days_ahead": 30,           // how far ahead to sync (default: 30)
            "county_filter": "Lee",     // only sync this county (default: all)
            "dry_run": false            // if true, returns events without writing
        }
    """
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "admin@shamrockbailbonds.biz")

    if not creds_path or not os.path.exists(creds_path):
        return JSONResponse(status_code=501, content={
            "success": False,
            "error": "Google Calendar not configured",
            "code": "GCAL_NOT_CONFIGURED",
            "setup_steps": [
                "1. Create a Google Cloud service account at console.cloud.google.com",
                "2. Enable the Google Calendar API",
                "3. Share your calendar with the service account email (Editor role)",
                "4. Download the service account JSON key",
                "5. Set GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json in .env",
                "6. Set GOOGLE_CALENDAR_ID=admin@shamrockbailbonds.biz in .env",
            ],
            "docs": "docs/GCAL_SYNC_SETUP.md",
        })

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        data = await request.json() or {}
        days_ahead = int(data.get("days_ahead", 30))
        county_filter = data.get("county_filter", "")
        dry_run = bool(data.get("dry_run", False))

        active_bonds = get_collection("active_bonds")
        now = _utc_now()
        window_end = now + timedelta(days=days_ahead)

        query: dict = {
            "status": {"$in": ["active", "released"]},
        }
        if county_filter:
            query["county"] = {"$regex": county_filter, "$options": "i"}

        cursor = active_bonds.find(query, {
            "_id": 0, "booking_number": 1, "defendant_name": 1,
            "court_date": 1, "court_location": 1, "case_number": 1, "county": 1
        })
        all_bonds = await cursor.to_list(500)

        # Filter to window (court_date may be string or datetime)
        bonds = []
        for b in all_bonds:
            cd = b.get("court_date")
            try:
                if isinstance(cd, str) and cd:
                    cd = datetime.fromisoformat(cd.replace("Z", "+00:00"))
                if isinstance(cd, datetime):
                    if cd.tzinfo is None:
                        cd = cd.replace(tzinfo=timezone.utc)
                    if now <= cd <= window_end:
                        b["_court_dt"] = cd
                        bonds.append(b)
            except Exception:
                continue

        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "events_to_sync": len(bonds),
                "bonds": [b.get("booking_number") for b in bonds],
            }

        scopes = ["https://www.googleapis.com/auth/calendar"]
        credentials = service_account.Credentials.from_service_account_file(
            creds_path, scopes=scopes
        )
        service = build("calendar", "v3", credentials=credentials)

        synced = 0
        errors = []
        for bond in bonds:
            try:
                court_dt = bond["_court_dt"]
                event = {
                    "summary": f"Court: {bond.get('defendant_name', 'Unknown')} ({bond.get('booking_number', '')})",
                    "location": bond.get("court_location", f"{bond.get('county', 'Lee')} County Justice Center"),
                    "description": (
                        f"Case: {bond.get('case_number', 'N/A')}\n"
                        f"Booking: {bond.get('booking_number', '')}\n"
                        f"County: {bond.get('county', '')}\n"
                        f"Synced by Shamrock Leads Dashboard"
                    ),
                    "start": {
                        "dateTime": court_dt.isoformat(),
                        "timeZone": "America/New_York",
                    },
                    "end": {
                        "dateTime": (court_dt + timedelta(hours=2)).isoformat(),
                        "timeZone": "America/New_York",
                    },
                    "reminders": {
                        "useDefault": False,
                        "overrides": [
                            {"method": "popup", "minutes": 60 * 24},
                            {"method": "popup", "minutes": 60 * 3},
                        ],
                    },
                }
                service.events().insert(calendarId=calendar_id, body=event).execute()
                synced += 1
            except Exception as ev_err:
                errors.append({"booking_number": bond.get("booking_number"), "error": str(ev_err)})

        return {
            "success": True,
            "synced": synced,
            "total": len(bonds),
            "errors": errors,
            "calendar_id": calendar_id,
        }

    except ImportError:
        return JSONResponse(status_code=501, content={
            "success": False,
            "error": "google-auth package not installed. Run: pip install google-auth google-api-python-client",
        })
    except Exception as e:
        logger.exception("[sync-gcal] Error: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)