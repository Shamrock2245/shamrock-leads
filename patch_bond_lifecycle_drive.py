import re

file_path = '/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/dashboard/routers/bond_lifecycle.py'

new_endpoint = """
@bond_lifecycle_bp.post("/file-to-drive/{packet_id}")
async def file_to_drive(request: Request, packet_id: str):
    \"\"\"
    Downloads the completed document group from SignNow and uploads it to Google Drive.
    Target Folder Hierarchy: Root -> DefendantName -> DefendantName_Date -> PDF
    \"\"\"
    from extensions import get_db
    from dashboard.services.google_drive_service import GoogleDriveService
    import datetime

    # Get document group ID from DB
    db = get_db()
    packet = await db.paperwork_packets.find_one({"packet_id": packet_id})
    if not packet:
        return JSONResponse({'error': 'Packet not found'}, status_code=404)
        
    group_id = packet.get("document_group_id")
    if not group_id:
        return JSONResponse({'error': 'No document group ID associated with this packet'}, status_code=400)
        
    # Get Defendant Name
    defendant_name = packet.get("defendant_name", "Unknown_Defendant")
    
    try:
        signnow_service = _get_signnow_service()
        pdf_bytes = await signnow_service.download_document_group(group_id)
        
        drive_service = GoogleDriveService()
        if not drive_service.is_configured:
            return JSONResponse({'error': 'Google Drive OAuth is not configured'}, status_code=500)
            
        root_folder_id = "1WnjwtxoaoXVW8_B6s-0ftdCPf_5WfKgs"
        
        def_folder_id = drive_service.get_or_create_folder(defendant_name, root_folder_id)
        if not def_folder_id:
            return JSONResponse({'error': 'Failed to get/create Defendant folder'}, status_code=500)
            
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        date_folder_name = f"{defendant_name}_{date_str}"
        date_folder_id = drive_service.get_or_create_folder(date_folder_name, def_folder_id)
        if not date_folder_id:
            return JSONResponse({'error': 'Failed to get/create Date folder'}, status_code=500)
            
        filename = f"{defendant_name}_Completed_Bond_{date_str}.pdf"
        link = drive_service.upload_pdf(pdf_bytes, filename, date_folder_id)
        
        if link:
            # Update DB with drive link
            await db.paperwork_packets.update_one(
                {"packet_id": packet_id},
                {"$set": {"drive_link": link, "status": "filed"}}
            )
            return JSONResponse({'status': 'success', 'drive_link': link})
        else:
            return JSONResponse({'error': 'Failed to upload PDF to Drive'}, status_code=500)
            
    except Exception as e:
        logger.error(f"Error in file_to_drive: {str(e)}")
        return JSONResponse({'error': str(e)}, status_code=500)
"""

with open(file_path, 'r') as f:
    content = f.read()

if "file-to-drive" not in content:
    with open(file_path, 'a') as f:
        f.write(new_endpoint)
    print("Endpoint file-to-drive added.")
else:
    print("Endpoint already exists.")
