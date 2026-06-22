import re

file_path = '/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/dashboard/routers/bond_lifecycle.py'

with open(file_path, 'r') as f:
    content = f.read()

# Replace the signature and logic
old_sig = """@bond_lifecycle_bp.post("/file-to-drive/{packet_id}")
async def file_to_drive(request: Request, packet_id: str):"""

new_sig = """@bond_lifecycle_bp.post("/file-to-drive/{identifier}")
async def file_to_drive(request: Request, identifier: str):"""

if old_sig in content:
    content = content.replace(old_sig, new_sig)
    
    old_query = 'packet = await db.paperwork_packets.find_one({"packet_id": packet_id})'
    new_query = 'packet = await db.paperwork_packets.find_one({"$or": [{"packet_id": identifier}, {"booking_number": identifier}]})'
    content = content.replace(old_query, new_query)
    
    with open(file_path, 'w') as f:
        f.write(content)
    print("Endpoint updated to support booking_number.")
else:
    print("Old signature not found.")
