"""
ShamrockLeads — Bond Lifecycle API Blueprint
Handles Phase 1 (indemnitor signing), Phase 2 (agent approval + POA),
SignNow completion webhook, and court email processing.

Migrated from Flask to Quart — all handlers are async.
"""

from quart import Blueprint, request, jsonify, current_app
import logging
import os

logger = logging.getLogger(__name__)
bond_lifecycle_bp = Blueprint('bond_lifecycle', __name__)


def _get_signnow_service():
    """Lazy-load SignNowPacketService to avoid import-time side effects."""
    from dashboard.services.signnow_packet_service import SignNowPacketService
    return SignNowPacketService()


def _get_calendar_service():
    """Lazy-load GoogleCalendarService."""
    from dashboard.services.google_calendar_service import GoogleCalendarService
    return GoogleCalendarService()


@bond_lifecycle_bp.route('/phase1/trigger', methods=['POST'])
async def trigger_phase_1():
    """
    Trigger Phase 1: Indemnitor signs first.
    Called when indemnitor submits intake form.
    """
    data = await request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    form_data = data.get('form_data', {})
    signer_email = data.get('signer_email')
    signer_name = data.get('signer_name')

    if not signer_email or not signer_name:
        return jsonify({'error': 'Missing signer email or name'}), 400

    try:
        signnow_service = _get_signnow_service()
        result = signnow_service.handle_send_phase_1(form_data, signer_email, signer_name)
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"Error in Phase 1 trigger: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bond_lifecycle_bp.route('/phase2/trigger', methods=['POST'])
async def trigger_phase_2():
    """
    Trigger Phase 2: Agent approval + POA entry.
    Called when bondsman approves bond in dashboard.
    """
    data = await request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    form_data = data.get('form_data', {})
    signer_email = data.get('signer_email')
    signer_name = data.get('signer_name')
    poa_number = data.get('poa_number')
    agent_name = data.get('agent_name')
    agent_license = data.get('agent_license')
    surety_id = data.get('surety_id', 'osi')

    if not all([signer_email, signer_name, poa_number, agent_name, agent_license]):
        return jsonify({'error': 'Missing required fields for Phase 2'}), 400

    try:
        signnow_service = _get_signnow_service()
        result = signnow_service.handle_send_phase_2(
            form_data, signer_email, signer_name,
            poa_number, agent_name, agent_license, surety_id
        )
        return jsonify(result), 200
    except ValueError as ve:
        return jsonify({'error': str(ve)}), 400
    except Exception as e:
        logger.error(f"Error in Phase 2 trigger: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bond_lifecycle_bp.route('/webhook/signnow/complete', methods=['POST'])
async def signnow_completion_webhook():
    """Handle SignNow document.complete webhook."""
    data = await request.get_json()
    logger.info(f"Received SignNow completion webhook: {data}")

    # In production:
    # 1. Verify webhook signature
    # 2. Download signed PDFs
    # 3. Upload to Google Drive case folder
    # 4. Update case status in DB
    # 5. Send Slack alert

    return jsonify({'status': 'received'}), 200


@bond_lifecycle_bp.route('/court-email/process', methods=['POST'])
async def process_court_email():
    """Endpoint to manually trigger or receive parsed court email data."""
    data = await request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    subject = data.get('subject', '')
    body = data.get('body', '')
    sender = data.get('sender', '')

    try:
        from dashboard.services.court_email_processor import CourtEmailProcessor
        processed_data = CourtEmailProcessor.process_email(subject, body, sender)

        calendar_service = _get_calendar_service()
        event = calendar_service.create_event(processed_data)

        return jsonify({
            'status': 'success',
            'processed_data': processed_data,
            'event_created': event is not None
        }), 200
    except Exception as e:
        logger.error(f"Error processing court email: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bond_lifecycle_bp.route('/court-emails/process-now', methods=['POST'])
async def process_gmail_now():
    """
    Manually trigger the Gmail → Calendar → BlueBubbles pipeline.
    Fetches unread court emails, processes them, creates calendar events,
    and sends iMessage notifications.
    """
    try:
        from dashboard.services.court_email_scheduler import CourtEmailScheduler
        # CourtEmailScheduler uses sync pymongo — create a sync client
        from pymongo import MongoClient
        mongo_uri = os.getenv("MONGODB_URI", "")
        db_name = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")
        sync_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000) if mongo_uri else None
        db = sync_client[db_name] if sync_client else None
        scheduler = CourtEmailScheduler(db=db)
        result = scheduler.process_all()
        return jsonify({
            'status': 'success',
            'emails_processed': result.get('processed', 0),
            'events_created': result.get('calendar_events_created', 0),
            'messages_sent': result.get('messages_sent', 0),
            'errors': result.get('errors', []),
        }), 200
    except Exception as e:
        logger.error(f"Gmail processing failed: {e}")
        return jsonify({'error': str(e)}), 500


@bond_lifecycle_bp.route('/errors', methods=['GET'])
async def get_error_log():
    """
    Query the self-hosted error log (MongoDB error_log collection).
    Params: ?source=scraper.lee&limit=50&level=error
    """
    try:
        from dashboard.services.error_tracker import ErrorTracker
        tracker = ErrorTracker()

        source = request.args.get('source')
        level = request.args.get('level')
        limit = int(request.args.get('limit', '50'))

        errors = tracker.get_recent_errors(
            source=source,
            level=level,
            limit=min(limit, 200),
        )
        return jsonify({
            'status': 'success',
            'count': len(errors),
            'errors': errors,
        }), 200
    except Exception as e:
        logger.error(f"Error log query failed: {e}")
        return jsonify({'error': str(e)}), 500


@bond_lifecycle_bp.route('/errors/stats', methods=['GET'])
async def get_error_stats():
    """Get aggregated error statistics (counts by source and level)."""
    try:
        from dashboard.services.error_tracker import ErrorTracker
        tracker = ErrorTracker()
        stats = tracker.get_error_stats()
        return jsonify({'status': 'success', 'stats': stats}), 200
    except Exception as e:
        logger.error(f"Error stats query failed: {e}")
        return jsonify({'error': str(e)}), 500



# ── Lifecycle Notes ───────────────────────────────────────────────────────────

@bond_lifecycle_bp.route('/lifecycle/notes/<booking_number>', methods=['POST'])
async def add_lifecycle_note(booking_number: str):
    """
    POST /api/bond-lifecycle/lifecycle/notes/<booking_number>
    Add a freeform note to a bond's lifecycle timeline.
    Body: { "note": "text", "source": "lifecycle_panel" }
    """
    try:
        from dashboard.extensions import get_db
        from datetime import datetime, timezone

        data = await request.get_json(force=True) or {}
        note_text = (data.get('note') or '').strip()
        if not note_text:
            return jsonify({'ok': False, 'error': 'Note text is required'}), 400

        source = data.get('source', 'dashboard')
        agent = data.get('agent', 'Staff')
        now = datetime.now(timezone.utc)

        db = get_db()

        # Try active_bonds first, then prospective_bonds
        note_entry = {
            'timestamp': now.isoformat(),
            'event': 'note_added',
            'detail': note_text[:500],
            'agent': agent,
            'source': source,
        }

        result = await db['active_bonds'].update_one(
            {'booking_number': booking_number},
            {'$push': {'timeline': note_entry}, '$set': {'updated_at': now}},
        )

        if result.matched_count == 0:
            result = await db['prospective_bonds'].update_one(
                {'booking_number': booking_number},
                {'$push': {'timeline': note_entry}, '$set': {'updated_at': now}},
            )

        if result.matched_count == 0:
            return jsonify({'ok': False, 'error': 'Bond not found'}), 404

        return jsonify({'ok': True, 'note': note_entry})
    except Exception as exc:
        logger.exception('add_lifecycle_note error')
        return jsonify({'ok': False, 'error': str(exc)}), 500
