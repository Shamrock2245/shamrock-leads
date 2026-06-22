import re
import sys

file_path = '/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/dashboard/routers/bond_lifecycle.py'

with open(file_path, 'r') as f:
    content = f.read()

new_endpoint = """
@bond_lifecycle_bp.post("/generate-packet")
async def generate_packet(request: Request):
    \"\"\"
    Unified endpoint to generate a SignNow packet with specific routing and custom manifests.
    \"\"\"
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
"""

if "generate-packet" not in content:
    with open(file_path, 'a') as f:
        f.write(new_endpoint)
    print("Endpoint added.")
else:
    print("Endpoint already exists.")
