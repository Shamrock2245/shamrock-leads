# ── AUTO-MIGRATED: Quart Blueprint → FastAPI APIRouter (v3) ──
# _qp = dict(request.query_params) injected into fns that read query params.
# Review each endpoint and move _qp.get() calls to typed fn signatures.

"""Court & Payment Reminders API Blueprint — BlueBubbles-Powered"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from dashboard.services.court_reminder_service import CourtReminderService
from dashboard.extensions import get_db, get_collection

court_reminders_bp = APIRouter(prefix="/api", tags=["court_reminders"])
@court_reminders_bp.post("/court-reminders/schedule")
async def schedule_reminders(request: Request):
    """Schedule 4-touch iMessage/SMS court reminders for defendant + indemnitors."""
    try:
        data = await request.json()
        booking_number = data.get("booking_number")
        defendant_name = data.get("defendant_name")
        phone = data.get("phone")
        court_date = data.get("court_date")
        court_location = data.get("court_location")
        case_number = data.get("case_number")
        indemnitor_phones = data.get("indemnitor_phones", [])

        if not all([booking_number, defendant_name, phone, court_date, court_location, case_number]):
            return JSONResponse({"error": "Missing required fields"}, status_code=400)

        service = CourtReminderService()
        result = await service.schedule_reminders(
            booking_number=booking_number,
            defendant_name=defendant_name,
            phone=phone,
            court_date_str=court_date,
            court_location=court_location,
            case_number=case_number,
            indemnitor_phones=indemnitor_phones,
        )
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@court_reminders_bp.post("/payment-reminders/schedule")
async def schedule_payment_reminders(request: Request):
    """Schedule 3-touch iMessage/SMS payment delinquency reminders."""
    try:
        data = await request.json()
        booking_number = data.get("booking_number")
        defendant_name = data.get("defendant_name")
        amount_due = data.get("amount_due")
        due_date = data.get("due_date")
        indemnitor_phones = data.get("indemnitor_phones", [])
        defendant_phone = data.get("defendant_phone", "")

        if not all([booking_number, defendant_name, amount_due, due_date]):
            return JSONResponse({"error": "Missing required fields"}, status_code=400)

        service = CourtReminderService()
        result = await service.schedule_payment_reminders(
            booking_number=booking_number,
            defendant_name=defendant_name,
            amount_due=float(amount_due),
            due_date_str=due_date,
            indemnitor_phones=indemnitor_phones,
            defendant_phone=defendant_phone,
        )
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@court_reminders_bp.post("/court-reminders/process")
async def process_reminders():
    """Cron endpoint — send all due reminders (court + payment) via BlueBubbles."""
    try:
        service = CourtReminderService()
        result = await service.process_due_reminders()
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@court_reminders_bp.post("/court-reminders/auto-scan")
async def court_reminders_auto_scan():
    """Trigger the hourly auto-scan: finds active bonds with court dates
    within 8 days and schedules 4-touch reminder sequences for unscheduled bonds.
    Also processes any due reminders immediately after scanning."""
    try:
        service = CourtReminderService()
        scan_result = await service.auto_scan_and_schedule()
        send_result = await service.process_due_reminders()
        return {
            "success": True,
            "scan": scan_result,
            "send": send_result,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@court_reminders_bp.get("/court-reminders/status")
async def court_reminders_status():
    """Returns reminder queue stats: pending/sent/failed counts + next due reminder."""
    try:
        db = get_db()
        col = db["court_reminders"]
        pending = await col.count_documents({"status": "pending"})
        sent = await col.count_documents({"status": "sent"})
        failed = await col.count_documents({"status": "failed"})
        cancelled = await col.count_documents({"status": {"$regex": "^cancelled"}})
        last_sent = await col.find_one(
            {"status": "sent"}, {"sent_at": 1, "_id": 0}, sort=[("sent_at", -1)]
        )
        next_due = await col.find_one(
            {"status": "pending"},
            {"send_at": 1, "defendant_name": 1, "touch": 1, "_id": 0},
            sort=[("send_at", 1)]
        )
        return {
            "success": True,
            "counts": {"pending": pending, "sent": sent, "failed": failed, "cancelled": cancelled},
            "last_sent_at": last_sent.get("sent_at") if last_sent else None,
            "next_due": {
                "send_at": next_due.get("send_at") if next_due else None,
                "defendant_name": next_due.get("defendant_name") if next_due else None,
                "touch": next_due.get("touch") if next_due else None,
            },
            "cron_interval_hours": 1,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@court_reminders_bp.get("/court-reminders/<booking_number>")
async def get_reminders(booking_number):
    """View scheduled/sent reminders for a case (both court + payment)."""
    try:
        col = get_collection("court_reminders")
        cursor = col.find(
            {"booking_number": booking_number}, {"_id": 0}
        ).sort("send_at", 1)
        reminders = await cursor.to_list(length=100)
        return {"reminders": reminders}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
