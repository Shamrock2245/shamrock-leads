"""Court & Payment Reminders API Blueprint — BlueBubbles-Powered"""

from quart import Blueprint, jsonify, request, current_app
from dashboard.services.court_reminder_service import CourtReminderService

court_reminders_bp = Blueprint("court_reminders", __name__)


@court_reminders_bp.route("/court-reminders/schedule", methods=["POST"])
async def schedule_reminders():
    """Schedule 4-touch iMessage/SMS court reminders for defendant + indemnitors."""
    try:
        data = await request.get_json()
        booking_number = data.get("booking_number")
        defendant_name = data.get("defendant_name")
        phone = data.get("phone")
        court_date = data.get("court_date")
        court_location = data.get("court_location")
        case_number = data.get("case_number")
        indemnitor_phones = data.get("indemnitor_phones", [])

        if not all([booking_number, defendant_name, phone, court_date, court_location, case_number]):
            return jsonify({"error": "Missing required fields"}), 400

        service = CourtReminderService(current_app.db)
        result = await service.schedule_reminders(
            booking_number=booking_number,
            defendant_name=defendant_name,
            phone=phone,
            court_date_str=court_date,
            court_location=court_location,
            case_number=case_number,
            indemnitor_phones=indemnitor_phones,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@court_reminders_bp.route("/payment-reminders/schedule", methods=["POST"])
async def schedule_payment_reminders():
    """Schedule 3-touch iMessage/SMS payment delinquency reminders."""
    try:
        data = await request.get_json()
        booking_number = data.get("booking_number")
        defendant_name = data.get("defendant_name")
        amount_due = data.get("amount_due")
        due_date = data.get("due_date")
        indemnitor_phones = data.get("indemnitor_phones", [])
        defendant_phone = data.get("defendant_phone", "")

        if not all([booking_number, defendant_name, amount_due, due_date]):
            return jsonify({"error": "Missing required fields"}), 400

        service = CourtReminderService(current_app.db)
        result = await service.schedule_payment_reminders(
            booking_number=booking_number,
            defendant_name=defendant_name,
            amount_due=float(amount_due),
            due_date_str=due_date,
            indemnitor_phones=indemnitor_phones,
            defendant_phone=defendant_phone,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@court_reminders_bp.route("/court-reminders/process", methods=["POST"])
async def process_reminders():
    """Cron endpoint — send all due reminders (court + payment) via BlueBubbles."""
    try:
        service = CourtReminderService(current_app.db)
        result = await service.process_due_reminders()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@court_reminders_bp.route("/court-reminders/<booking_number>", methods=["GET"])
async def get_reminders(booking_number):
    """View scheduled/sent reminders for a case (both court + payment)."""
    try:
        cursor = current_app.db["court_reminders"].find(
            {"booking_number": booking_number}, {"_id": 0}
        ).sort("send_at", 1)
        reminders = await cursor.to_list(length=100)
        return jsonify({"reminders": reminders})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
