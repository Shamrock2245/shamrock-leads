from fastapi.responses import JSONResponse
from fastapi import APIRouter, Query, Request
"""
ShamrockLeads — Bond Lifecycle API Blueprint
Handles Phase 1 (indemnitor signing), Phase 2 (agent approval + POA),
SignNow completion webhook, and court email processing.

Migrated from Flask to Quart — all handlers are async.
"""
import logging
import os

logger = logging.getLogger(__name__)
bond_lifecycle_bp = APIRouter(prefix="/api", tags=["bond_lifecycle"])
def _get_signnow_service():
    """Lazy-load SignNowPacketService to avoid import-time side effects."""
    from dashboard.services.signnow_packet_service import SignNowPacketService
    return SignNowPacketService()


def _get_calendar_service():
    """Lazy-load GoogleCalendarService."""
    from dashboard.services.google_calendar_service import GoogleCalendarService
    return GoogleCalendarService()


@bond_lifecycle_bp.get("/paperwork/config")
async def get_paperwork_config():
    """Returns the SignNow DOC_RULES and TEMPLATE_MAP for UI inspection."""
    signnow_service = _get_signnow_service()
    return JSONResponse(status_code=200, content={
        'status': 'success',
        'doc_rules': signnow_service.DOC_RULES,
        'template_map': signnow_service.TEMPLATE_MAP
    })



@bond_lifecycle_bp.post("/phase1/trigger")
async def trigger_phase_1(request: Request):
    """
    Trigger Phase 1: Indemnitor signs first.
    Called when indemnitor submits intake form.
    """
    data = await request.json()
    if not data:
        return JSONResponse({'error': 'No data provided'}, status_code=400)

    from extensions import get_db
    from bson.objectid import ObjectId
    db = get_db()
    
    intake_id = data.get('intake_id')
    booking_number = data.get('booking_number')
    
    intake_doc = None
    if intake_id:
        intake_doc = await db.intake_queue.find_one({"_id": ObjectId(intake_id)})
    if not intake_doc and booking_number:
        intake_doc = await db.intake_queue.find_one({"booking_number": booking_number})
        
    if not intake_doc:
        return JSONResponse({'error': 'Could not locate intake record for phase 1'}, status_code=404)

    signer_email = data.get('signer_email') or intake_doc.get("indemnitor_email")
    signer_name = data.get('signer_name') or intake_doc.get("indemnitor_name")
    surety_id = data.get('surety_id') or intake_doc.get("surety_id", "osi")

    if not signer_email or not signer_name:
        return JSONResponse({'error': 'Missing signer email or name'}, status_code=400)

    try:
        signnow_service = _get_signnow_service()
        result = await signnow_service.send_phase_1(
            intake_doc=intake_doc, 
            signer_email=signer_email, 
            signer_name=signer_name,
            surety_id=surety_id
        )
        return JSONResponse(status_code=200, content=result)
    except Exception as e:
        logger.error(f"Error in Phase 1 trigger: {str(e)}")
        return JSONResponse({'error': str(e)}, status_code=500)


@bond_lifecycle_bp.post("/phase2/trigger")
async def trigger_phase_2(request: Request):
    """
    Trigger Phase 2: Agent approval + POA entry.
    Called when bondsman approves bond in dashboard.
    """
    data = await request.json()
    if not data:
        return JSONResponse({'error': 'No data provided'}, status_code=400)

    from extensions import get_db
    from bson.objectid import ObjectId
    db = get_db()
    
    intake_id = data.get('intake_id')
    booking_number = data.get('booking_number')
    
    intake_doc = None
    if intake_id:
        intake_doc = await db.intake_queue.find_one({"_id": ObjectId(intake_id)})
    if not intake_doc and booking_number:
        intake_doc = await db.intake_queue.find_one({"booking_number": booking_number})
        
    if not intake_doc:
        return JSONResponse({'error': 'Could not locate intake record for phase 2'}, status_code=404)

    signer_email = data.get('signer_email') or intake_doc.get("indemnitor_email")
    signer_name = data.get('signer_name') or intake_doc.get("indemnitor_name")
    poa_number = data.get('poa_number')
    surety_id = data.get('surety_id', 'osi')

    if not poa_number:
        return JSONResponse({'error': 'Missing POA number for Phase 2'}, status_code=400)
    if not signer_email or not signer_name:
        return JSONResponse({'error': 'Missing signer email or name'}, status_code=400)

    try:
        signnow_service = _get_signnow_service()
        result = await signnow_service.send_phase_2(
            intake_doc=intake_doc,
            signer_email=signer_email,
            signer_name=signer_name,
            poa_number=poa_number,
            surety_id=surety_id
        )
        return JSONResponse(status_code=200, content=result)
    except ValueError as ve:
        return JSONResponse({'error': str(ve)}, status_code=400)
    except Exception as e:
        logger.error(f"Error in Phase 2 trigger: {str(e)}")
        return JSONResponse({'error': str(e)}, status_code=500)


@bond_lifecycle_bp.post("/webhook/signnow/complete")
async def signnow_completion_webhook(request: Request):
    """Handle SignNow document.complete webhook."""
    data = await request.json()
    logger.info(f"Received SignNow completion webhook: {data}")

    # In production:
    # 1. Verify webhook signature
    # 2. Download signed PDFs
    # 3. Upload to Google Drive case folder
    # 4. Update case status in DB
    # 5. Send Slack alert

    return JSONResponse(status_code=200, content={'status': 'received'})


@bond_lifecycle_bp.post("/court-email/process")
async def process_court_email(request: Request):
    """Endpoint to manually trigger or receive parsed court email data."""
    data = await request.json()
    if not data:
        return JSONResponse({'error': 'No data provided'}, status_code=400)

    subject = data.get('subject', '')
    body = data.get('body', '')
    sender = data.get('sender', '')

    try:
        from dashboard.services.court_email_processor import CourtEmailProcessor
        processed_data = CourtEmailProcessor.process_email(subject, body, sender)

        calendar_service = _get_calendar_service()
        event = calendar_service.create_event(processed_data)

        return JSONResponse(status_code=200, content={
            'status': 'success',
            'processed_data': processed_data,
            'event_created': event is not None
        })
    except Exception as e:
        logger.error(f"Error processing court email: {str(e)}")
        return JSONResponse({'error': str(e)}, status_code=500)


@bond_lifecycle_bp.post("/court-emails/process-now")
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
        return JSONResponse(status_code=200, content={
            'status': 'success',
            'emails_processed': result.get('processed', 0),
            'events_created': result.get('calendar_events_created', 0),
            'messages_sent': result.get('messages_sent', 0),
            'errors': result.get('errors', []),
        })
    except Exception as e:
        logger.error(f"Gmail processing failed: {e}")
        return JSONResponse({'error': str(e)}, status_code=500)


@bond_lifecycle_bp.get("/errors")
async def get_error_log(
    source: str = Query(default=None),
    level: str = Query(default=None),
    limit: int = Query(default=50),
):
    """
    Query the self-hosted error log (MongoDB error_log collection).
    Params: ?source=scraper.lee&limit=50&level=error
    """
    try:
        from dashboard.services.error_tracker import ErrorTracker
        tracker = ErrorTracker()

        errors = tracker.get_recent_errors(
            source=source,
            level=level,
            limit=min(limit, 200),
        )
        return JSONResponse(status_code=200, content={
            'status': 'success',
            'count': len(errors),
            'errors': errors,
        })
    except Exception as e:
        logger.error(f"Error log query failed: {e}")
        return JSONResponse({'error': str(e)}, status_code=500)


@bond_lifecycle_bp.get("/errors/stats")
async def get_error_stats():
    """Get aggregated error statistics (counts by source and level)."""
    try:
        from dashboard.services.error_tracker import ErrorTracker
        tracker = ErrorTracker()
        stats = tracker.get_error_stats()
        return JSONResponse(status_code=200, content={'status': 'success', 'stats': stats})
    except Exception as e:
        logger.error(f"Error stats query failed: {e}")
        return JSONResponse({'error': str(e)}, status_code=500)



# ── Lifecycle Notes ───────────────────────────────────────────────────────────

@bond_lifecycle_bp.post("/lifecycle/notes/{booking_number}")
async def add_lifecycle_note(request: Request, booking_number: str):
    """
    POST /api/bond-lifecycle/lifecycle/notes/<booking_number>
    Add a freeform note to a bond's lifecycle timeline.
    Body: { "note": "text", "source": "lifecycle_panel" }
    """
    try:
        from dashboard.extensions import get_db
        from datetime import datetime, timezone

        data = await request.json() or {}
        note_text = (data.get('note') or '').strip()
        if not note_text:
            return JSONResponse({'ok': False, 'error': 'Note text is required'}, status_code=400)

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
            return JSONResponse({'ok': False, 'error': 'Bond not found'}, status_code=404)

        return {'ok': True, 'note': note_entry}
    except Exception as exc:
        logger.exception('add_lifecycle_note error')
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=500)
@bond_lifecycle_bp.post("/generate-packet")
async def generate_packet(request: Request):
    """
    Unified endpoint to generate a SignNow packet with specific routing and custom manifests.
    """
    data = await request.json()
    if not data:
        return JSONResponse({'error': 'No data provided'}, status_code=400)

    from extensions import get_db
    from bson.objectid import ObjectId
    db = get_db()
    
    intake_id = data.get('intake_id')
    booking_number = data.get('booking_number')
    
    intake_doc = None
    if intake_id:
        intake_doc = await db.intake_queue.find_one({"_id": ObjectId(intake_id)})
    if not intake_doc and booking_number:
        intake_doc = await db.intake_queue.find_one({"booking_number": booking_number})
        
    if not intake_doc:
        # We might not have a formal intake, just use form_data
        intake_doc = data.get('form_data', {})
        intake_doc['intake_id'] = str(ObjectId()) # dummy ID if none
        if not intake_doc.get('booking_number'):
            intake_doc['booking_number'] = booking_number

    signer_email = data.get('signer_email')
    signer_name = data.get('signer_name')
    surety_id = data.get('surety_id', 'osi')
    poa_number = data.get('poa_number')
    routing_scenario = data.get('routing_scenario', 'phase1_2')
    custom_manifest = data.get('custom_manifest')

    if not signer_email or not signer_name:
        return JSONResponse({'error': 'Missing signer email or name'}, status_code=400)

    # In case of Phase 1, we still might just pass custom manifest to Phase 1 trigger or directly to create_packet.
    try:
        signnow_service = _get_signnow_service()
        import uuid
        packet_id = str(uuid.uuid4())
        
        # We handle routing scenario explicitly inside create_packet now.
        res = await signnow_service.create_packet(
            intake_doc=intake_doc,
            packet_id=packet_id,
            phase=1 if routing_scenario == 'phase1_2' else 0, # Phase 0 meaning all_in_one
            surety_id=surety_id,
            signer_email=signer_email,
            signer_name=signer_name,
            routing_scenario=routing_scenario,
            custom_manifest=custom_manifest,
            poa_number=poa_number
        )
        return JSONResponse(status_code=200, content=res)
    except Exception as e:
        logger.error(f"Error in unified packet generation: {str(e)}")
        return JSONResponse({'error': str(e)}, status_code=500)

@bond_lifecycle_bp.post("/file-to-drive/{identifier}")
async def file_to_drive(request: Request, identifier: str):
    """
    Downloads the completed document group from SignNow and uploads it to Google Drive.
    Target Folder Hierarchy: Root -> DefendantName -> DefendantName_Date -> PDF
    """
    from extensions import get_db
    from dashboard.services.google_drive_service import GoogleDriveService
    import datetime

    # Get document group ID from DB
    db = get_db()
    packet = await db.paperwork_packets.find_one({"$or": [{"packet_id": identifier}, {"booking_number": identifier}]})
    if not packet:
        return JSONResponse({'error': 'Packet not found'}, status_code=404)
        
    group_id = packet.get("document_group_id")
    if not group_id:
        return JSONResponse({'error': 'No document group ID associated with this packet'}, status_code=400)
        
    # Get Defendant Name and surety for correct Drive folder routing
    defendant_name = packet.get("defendant_name", "Unknown_Defendant")
    # Normalise surety_id from packet (stored as 'osi' or 'palmetto')
    surety_id = (packet.get("surety_id") or packet.get("insurance_company") or "osi").lower().strip()
    if surety_id not in ("osi", "palmetto"):
        surety_id = "osi"
    packet_id = packet.get("packet_id", identifier)  # fix undefined variable
    
    try:
        signnow_service = _get_signnow_service()
        pdf_bytes = await signnow_service.download_document_group(group_id)
        
        drive_service = GoogleDriveService()
        if not drive_service.is_configured:
            return JSONResponse({'error': 'Google Drive OAuth is not configured'}, status_code=500)

        # ── Drive Folder Hierarchy ──
        # Root (Completed Bonds)
        #   └─ OSI /  Palmetto          ← surety subfolder (GAP-G fix)
        #       └─ DefendantLastName, FirstInitial_YYYYMMDD
        #           └─ PDF file
        root_folder_id = "1WnjwtxoaoXVW8_B6s-0ftdCPf_5WfKgs"

        # Surety subfolder (OSI or Palmetto)
        surety_label = surety_id.upper()  # 'OSI' or 'PALMETTO'
        surety_folder_id = drive_service.get_or_create_folder(surety_label, root_folder_id)
        if not surety_folder_id:
            return JSONResponse({'error': f'Failed to get/create surety folder ({surety_label})'}, status_code=500)

        # Defendant subfolder — naming convention: LastName, FirstInitial_YYYYMMDD
        date_str = datetime.datetime.now().strftime("%Y%m%d")
        # Try to build canonical name from parts; fall back to full name
        def_last = packet.get("defendant_last_name") or ""
        def_first = packet.get("defendant_first_name") or ""
        if def_last and def_first:
            folder_name = f"{def_last}, {def_first[0].upper()}_{date_str}"
        else:
            folder_name = f"{defendant_name.replace(' ', '_')}_{date_str}"

        def_folder_id = drive_service.get_or_create_folder(folder_name, surety_folder_id)
        if not def_folder_id:
            return JSONResponse({'error': 'Failed to get/create Defendant folder'}, status_code=500)

        filename = f"{folder_name}_Completed_Bond.pdf"
        link = drive_service.upload_pdf(pdf_bytes, filename, def_folder_id)
        
        if link:
            # Update DB with drive link, surety, and filed status
            await db.paperwork_packets.update_one(
                {"packet_id": packet_id},
                {"$set": {
                    "drive_link": link,
                    "drive_folder_id": def_folder_id,
                    "surety_id": surety_id,
                    "status": "filed",
                    "filed_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }}
            )
            logger.info("[file-to-drive] Filed %s (%s) → Drive: %s", defendant_name, surety_label, link)
            return JSONResponse({'status': 'success', 'drive_link': link, 'surety': surety_label})
        else:
            return JSONResponse({'error': 'Failed to upload PDF to Drive'}, status_code=500)
            
    except Exception as e:
        logger.error(f"Error in file_to_drive: {str(e)}")
        return JSONResponse({'error': str(e)}, status_code=500)
