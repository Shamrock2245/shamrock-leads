import asyncio
import os
import sys
import uuid
import json

# Add current dir to path to resolve dashboard
sys.path.append(os.getcwd())

# Load environment
from dotenv import load_dotenv
load_dotenv('.env')

from dashboard.services.signnow_packet_service import SignNowPacketService
from dashboard.services.audit_service import AuditService
from dashboard.services.state_machine import BondStateMachine
from dashboard.extensions import get_collection

async def run_e2e_test():
    print("--- Starting End-to-End SignNow Group Flow Test ---")
    if os.getenv("SIGNNOW_BRAND_ID"):
        print(f"--- Branding Configured: {os.getenv('SIGNNOW_BRAND_ID')} ---")
    else:
        print("--- No Branding Configured (Set SIGNNOW_BRAND_ID to test white-labeling) ---")

    # 1. Setup Mock Intake Document
    mock_booking_number = f"TEST-{uuid.uuid4().hex[:8].upper()}"
    packet_id = str(uuid.uuid4())
    
    intake_doc = {
        "intake_id": str(uuid.uuid4()),
        "booking_number": mock_booking_number,
        "defendant_name": "John Doe E2E",
        "defendant_email": "defendant_e2e@example.com",
        "indemnitor_name": "Jane Smith Indemnitor",
        "indemnitor_email": "indemnitor_e2e@example.com",
        "indemnitor_phone": "+15555555555",
        "bond_amount": "5000",
        "surety_id": "osi"
    }
    
    # Pre-insert a mock bond_case into the DB so the state machine has something to transition
    active_bonds_col = get_collection("active_bonds")
    mock_bond_case = {
        "bond_case_id": str(uuid.uuid4()),
        "booking_number": mock_booking_number,
        "defendant_name": "John Doe E2E",
        "status": "pending",  # initially pending
        "packet_id": packet_id
    }
    await active_bonds_col.insert_one(mock_bond_case)
    print(f"1. Created Mock Bond Case: {mock_bond_case['bond_case_id']} with booking number {mock_booking_number}")

    # 2. Call SignNowPacketService
    signnow_service = SignNowPacketService()
    
    print(f"2. Generating Document Group Packet via SignNow API (Phase1_2) for Surety OSI...")
    try:
        # Since it actually hits SignNow, we want to see if it generates invites successfully
        # Wait, does it use actual templates? Yes, from TEMPLATE_MAP.
        # This will create a real document group in SignNow Sandbox or Prod!
        # Make sure this account is ok with creating a test document.
        result = await signnow_service.create_packet(
            intake_doc=intake_doc,
            packet_id=packet_id,
            phase=0, # not used directly for phase1_2
            surety_id="osi",
            signer_email=intake_doc["indemnitor_email"],
            signer_name=intake_doc["indemnitor_name"],
            routing_scenario="phase1_2",
            poa_number="TEST-POA-001"
        )
        print(" -> SignNow Packet Creation SUCCESS!")
        print(" -> Output:")
        print(json.dumps(result, indent=2))
        
        group_id = result.get('document_group_id')
        invites = result.get('invites', [])
        print(f" -> Group ID: {group_id}")
        for inv in invites:
            print(f"    - Role: {inv.get('role_name')}, Link: {inv.get('link')}")
            
    except Exception as e:
        print(f" -> SignNow Packet Creation FAILED: {str(e)}")
        import traceback
        traceback.print_exc()
        return

    # 3. Simulate Webhook
    print("\n3. Simulating document_group.complete webhook...")
    
    # Add a mock paperwork_packets entry since the webhook looks for it
    packets_col = get_collection("paperwork_packets")
    await packets_col.insert_one({
        "packet_id": packet_id,
        "signnow_group_id": group_id,
        "booking_number": mock_booking_number,
        "bond_case_id": mock_bond_case["bond_case_id"],
        "status": "sent"
    })
    
    try:
        # Here we simulate exactly what Step 8b in webhooks.py does
        # Update packet status to completed (as webhooks.py does)
        await packets_col.update_one(
            {"packet_id": packet_id},
            {"$set": {"status": "completed"}}
        )

        print(f" -> Logging to CRM Activity Feed...")
        await AuditService.log_event(
            entity_type="bond_case",
            entity_id=mock_booking_number,
            action="Document Group Completed",
            details={"reason": f"All required signatures collected for Packet {packet_id}"},
            actor="System (SignNow)",
            actor_type="system",
            event_context=str({
                "module": "paperwork",
                "signnow_document_id": group_id,
                "packet_id": packet_id,
                "defendant_name": intake_doc["defendant_name"]
            })
        )
        
        print(f" -> Transitioning Bond State to Active...")
        await BondStateMachine.transition_bond(
            booking_number=mock_booking_number,
            new_status="active",
            actor="System (SignNow Webhook)",
            reason=f"Document Group {group_id} signed"
        )
        print(" -> Webhook Simulation SUCCESS!")
        
        # Validate final state
        final_bond = await active_bonds_col.find_one({"booking_number": mock_booking_number})
        print(f" -> Final Bond Status: {final_bond.get('status')} (Expected: active)")
        
        audit_col = get_collection("audit_events")
        audits = await audit_col.find({"entity_id": mock_booking_number}).to_list(None)
        print(f" -> Audit Events Logged: {len(audits)}")
        for a in audits:
            print(f"    - [{a.get('timestamp')}] {a.get('actor_type')}/{a.get('actor')}: {a.get('action')}")
            
    except Exception as e:
        print(f" -> Webhook Simulation FAILED: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run_e2e_test())
