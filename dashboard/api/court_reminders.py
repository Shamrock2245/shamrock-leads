"""Court Reminders API Blueprint (Phase 2)"""
from quart import Blueprint, jsonify, request
from datetime import datetime, timezone
from dashboard.extensions import get_collection
from dashboard.services.court_reminder_service import CourtReminderService

court_reminders_bp = Blueprint('court_reminders', __name__)
reminder_service = CourtReminderService()


@court_reminders_bp.route('/court-reminders/schedule', methods=['POST'])
async def schedule_reminders():
    """Schedule 4-touch reminders for a defendant."""
    try:
        data = await request.get_json()
        booking_number = data.get("booking_number")
        defendant_name = data.get("defendant_name")
        phone = data.get("phone")
        court_date = data.get("court_date")
        court_location = data.get("court_location")
        case_number = data.get("case_number")

        if not all([booking_number, defendant_name, phone, court_date, court_location, case_number]):
            return jsonify({"error": "Missing required fields"}), 400

        result = await reminder_service.schedule_reminders(
            booking_number=booking_number,
            defendant_name=defendant_name,
            phone=phone,
            court_date_str=court_date,
            court_location=court_location,
            case_number=case_number,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@court_reminders_bp.route('/court-reminders/process', methods=['POST'])
async def process_reminders():
    """Cron endpoint: find all due reminders and send them."""
    try:
        result = await reminder_service.process_due_reminders()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@court_reminders_bp.route('/court-reminders/<booking_number>', methods=['GET'])
async def get_reminders(booking_number):
    """View scheduled/sent reminders for a case."""
    try:
        court_reminders = get_collection("court_reminders")
        cursor = court_reminders.find(
            {"booking_number": booking_number}, {"_id": 0}
        ).sort("send_at", 1)
        reminders = await cursor.to_list(length=100)
        return jsonify({"reminders": reminders})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
