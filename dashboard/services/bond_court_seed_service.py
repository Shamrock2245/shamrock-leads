"""Seed Google Calendar + BlueBubbles court reminders when a bond is written.

Complements the email-scrape path (CourtEmailScheduler). Call this on intake
promote / DocuSeal finalize / bond activate when court_date is known.

Idempotent via bond.gcal_event_id + court_date fingerprint, and via
CourtReminderService.cancel + reschedule / existing pending check.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)


def _fingerprint(court_date: str, court_time: str = "") -> str:
    return f"{str(court_date or '').strip()}|{str(court_time or '').strip()}"


def _bond_email_data(bond: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map active_bonds fields into GoogleCalendarService.create_event shape."""
    court_date = str(bond.get("court_date") or "").strip()
    if not court_date or court_date.upper() in ("TBN", "TBD", "UNKNOWN", "N/A"):
        return None

    court_time = str(bond.get("court_time") or "").strip() or "09:00 AM"
    case_number = (
        str(bond.get("case_number") or "").strip()
        or str(bond.get("booking_number") or "").strip()
    )
    if not case_number:
        return None

    # Normalize ISO date-only to M/D/Y for Calendar parser friendliness
    date_str = court_date
    if "T" in court_date:
        date_str = court_date.split("T", 1)[0]
    if len(date_str) == 10 and date_str[4] == "-":
        # YYYY-MM-DD → keep; GoogleCalendarService accepts %Y-%m-%d
        pass

    return {
        "case_number": case_number,
        "datetime_info": {"date_str": date_str, "time_str": court_time},
        "event_type": "courtDate",
        "defendant_name": bond.get("defendant_name") or "Unknown Defendant",
        "defendant_email": bond.get("defendant_email") or "",
        "county": bond.get("county") or "",
        "judge": bond.get("judge") or "",
        "location": bond.get("court_location") or "",
        "subject": f"Bond write seed — {bond.get('booking_number') or case_number}",
        "sender": "shamrock-leads/bond_court_seed",
        "booking_number": bond.get("booking_number") or "",
    }


async def seed_court_calendar_for_bond(
    bond: Optional[Dict[str, Any]] = None,
    *,
    booking_number: str = "",
    source: str = "bond_write",
    schedule_reminders: bool = True,
) -> Dict[str, Any]:
    """Create GCal event + schedule BB court reminders for one active bond.

    Soft-fails: never raises to callers. When Calendar OAuth is missing, stamps
    staff-visible flags on the bond instead of silent dry-run-only.
    """
    outcome: Dict[str, Any] = {
        "success": False,
        "skipped": False,
        "source": source,
        "gcal": None,
        "reminders": None,
    }
    try:
        bonds_col = get_collection("active_bonds")
        if bond is None and booking_number:
            bond = await bonds_col.find_one({"booking_number": booking_number})
        if not bond:
            outcome["reason"] = "bond_not_found"
            return outcome

        booking_number = str(bond.get("booking_number") or booking_number or "").strip()
        outcome["booking_number"] = booking_number

        email_data = _bond_email_data(bond)
        if not email_data:
            outcome["success"] = True
            outcome["skipped"] = True
            outcome["reason"] = "no_court_date"
            return outcome

        fp = _fingerprint(bond.get("court_date") or "", bond.get("court_time") or "")
        existing_id = bond.get("gcal_event_id")
        existing_fp = bond.get("gcal_seed_fingerprint")
        if existing_id and existing_fp == fp and not str(existing_id).startswith("dryrun:"):
            outcome["success"] = True
            outcome["skipped"] = True
            outcome["reason"] = "already_seeded"
            outcome["gcal_event_id"] = existing_id
            # Still ensure reminders if never scheduled
            if schedule_reminders and not bond.get("court_reminders_seeded_at"):
                outcome["reminders"] = await _schedule_reminders_for_bond(bond, email_data)
            return outcome

        from dashboard.services.google_calendar_service import GoogleCalendarService

        cal = GoogleCalendarService()
        oauth_ok = bool(cal.is_configured)
        now_iso = datetime.now(timezone.utc).isoformat()

        event = None
        gcal_status = "failed"
        gcal_note = ""
        gcal_event_id = None

        if not oauth_ok:
            gcal_status = "oauth_missing"
            gcal_note = (
                "Google Calendar OAuth not configured "
                "(GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_GMAIL_REFRESH_TOKEN). "
                "Court date was NOT written to admin@ calendar — staff: use Sync GCal "
                "or restore Calendar OAuth."
            )
            logger.error(
                "[bond_court_seed] OAuth missing — cannot seed GCal for booking=%s "
                "source=%s. Staff-visible flag set on bond.",
                booking_number,
                source,
            )
            # Still invoke create_event so dry-run body is logged with clear marker
            try:
                event = cal.create_event(email_data)
            except Exception as exc:
                logger.warning("[bond_court_seed] dry-run create_event error: %s", exc)
                event = None
            if isinstance(event, dict) and event.get("dry_run"):
                gcal_event_id = f"dryrun:{fp}"
        else:
            try:
                event = cal.create_event(email_data)
            except Exception as exc:
                logger.exception(
                    "[bond_court_seed] create_event failed booking=%s: %s",
                    booking_number,
                    exc,
                )
                gcal_status = "failed"
                gcal_note = str(exc)[:300]
                event = None

            if event is None:
                # Duplicate skip or parse failure
                gcal_status = "duplicate_or_skipped"
                gcal_note = "create_event returned None (duplicate or unparseable)"
                # Keep prior id if any
                gcal_event_id = existing_id
            elif event.get("dry_run"):
                gcal_status = "oauth_missing"
                gcal_note = "Calendar service returned dry_run despite is_configured"
                gcal_event_id = f"dryrun:{fp}"
                logger.error(
                    "[bond_court_seed] Unexpected dry_run with OAuth configured booking=%s",
                    booking_number,
                )
            else:
                gcal_event_id = event.get("id")
                gcal_status = "created" if gcal_event_id else "created_no_id"
                gcal_note = ""

        bond_set: Dict[str, Any] = {
            "gcal_seed_status": gcal_status,
            "gcal_seed_note": gcal_note,
            "gcal_seed_source": source,
            "gcal_seed_at": now_iso,
            "gcal_seed_fingerprint": fp,
            "updated_at": datetime.now(timezone.utc),
        }
        if gcal_event_id:
            bond_set["gcal_event_id"] = gcal_event_id
        if gcal_status == "oauth_missing":
            # Staff-visible operational flag (Kanban / bond detail can surface)
            bond_set["gcal_oauth_missing"] = True
            bond_set["staff_notes_gcal"] = gcal_note
        else:
            bond_set["gcal_oauth_missing"] = False

        try:
            await bonds_col.update_one(
                {"booking_number": booking_number},
                {"$set": bond_set},
            )
        except Exception as db_err:
            logger.warning("[bond_court_seed] bond stamp failed: %s", db_err)

        outcome["gcal"] = {
            "status": gcal_status,
            "event_id": gcal_event_id,
            "note": gcal_note,
            "oauth_configured": oauth_ok,
        }
        outcome["gcal_event_id"] = gcal_event_id

        if schedule_reminders:
            outcome["reminders"] = await _schedule_reminders_for_bond(bond, email_data)

        outcome["success"] = gcal_status in (
            "created",
            "created_no_id",
            "duplicate_or_skipped",
            "already_seeded",
        ) or (
            # OAuth missing is a soft success for bond-write path: reminders may still work
            gcal_status == "oauth_missing"
        )
        if gcal_status == "oauth_missing":
            outcome["reason"] = "oauth_missing"
        return outcome

    except Exception as exc:
        logger.exception("[bond_court_seed] soft-fail (%s): %s", source, exc)
        outcome["error"] = str(exc)[:300]
        return outcome


async def _schedule_reminders_for_bond(
    bond: Dict[str, Any],
    email_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Cancel pending + schedule CourtReminderService 4-touch sequence."""
    booking_number = str(bond.get("booking_number") or "").strip()
    try:
        from dashboard.services.court_reminder_service import (
            CourtReminderService,
            parse_court_date_string,
        )

        date_str = (email_data.get("datetime_info") or {}).get("date_str") or ""
        time_str = (email_data.get("datetime_info") or {}).get("time_str") or "09:00 AM"
        combined = f"{date_str} {time_str}".strip()
        court_dt = parse_court_date_string(combined)
        if court_dt is None:
            return {"success": False, "error": "unparseable_court_date", "raw": combined}

        court_date_str = court_dt.isoformat()
        phone = (
            bond.get("defendant_phone")
            or bond.get("phone")
            or ""
        )
        ind_phone = bond.get("indemnitor_phone") or ""
        if isinstance(bond.get("indemnitor"), dict):
            ind_phone = ind_phone or (bond["indemnitor"].get("phone") or "")

        # If no defendant phone, still schedule for indemnitor (service accepts phone="")
        svc = CourtReminderService()
        await svc.cancel_reminders(booking_number)
        result = await svc.schedule_reminders(
            booking_number=booking_number,
            defendant_name=bond.get("defendant_name") or "Defendant",
            phone=str(phone or ind_phone or ""),
            court_date_str=court_date_str,
            court_location=bond.get("court_location")
            or email_data.get("location")
            or "",
            case_number=email_data.get("case_number") or "",
            indemnitor_phones=[ind_phone] if ind_phone else [],
        )

        try:
            bonds_col = get_collection("active_bonds")
            await bonds_col.update_one(
                {"booking_number": booking_number},
                {
                    "$set": {
                        "court_reminders_seeded_at": datetime.now(timezone.utc).isoformat(),
                        "court_reminders_scheduled": bool(
                            (result or {}).get("success")
                            or (result or {}).get("scheduled")
                        ),
                    }
                },
            )
        except Exception:
            pass

        return result if isinstance(result, dict) else {"result": result}
    except Exception as exc:
        logger.warning(
            "[bond_court_seed] reminder schedule failed booking=%s: %s",
            booking_number,
            exc,
        )
        return {"success": False, "error": str(exc)[:300]}
