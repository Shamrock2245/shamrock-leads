"""
E2E Test: Paperwork Packet -> DocuSeal -> Google Drive
"""
import asyncio
import os
import sys

# Ensure imports work from project root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dashboard.services.docuseal_service import DocuSealService

async def main():
    print("🚀 Starting E2E Paperwork Test")
    
    ds = DocuSealService()
    if not ds.is_configured:
        print("❌ DocuSeal is not configured. Check DOCUSEAL_API_KEY in .env")
        sys.exit(1)

    print("✅ DocuSeal service initialized.")

    # 1. Create a synthetic bond case
    bond_data = {
        "defendant_name": "E2E Test Defendant",
        "indemnitor_name": "E2E Test Indemnitor",
        "indemnitor_email": "admin@shamrockbailbonds.biz",
        "defendant_email": "admin@shamrockbailbonds.biz",
        "county": "Lee",
        "case_number": "E2E-TEST-CASE",
        "poa_number": "OSI-TEST-1234",
        "booking_number": "E2E-BK-999",
        "bond_amount": 1000,
        "charge_details": [
            {"charge": "TESTING E2E", "bond_amount": 1000, "case_number": "E2E-TEST-CASE", "poa_number": "OSI-TEST-1234"}
        ]
    }
    
    template_id = int(os.environ.get("DOCUSEAL_TEMPLATE_ID_OSI", 1))
    packet_id = "test-packet-e2e-123"
    
    print(f"📦 Generating submission for packet_id={packet_id}...")
    try:
        submission = await ds.create_submission_for_packet(
            template_id=template_id,
            packet_id=packet_id,
            bond_data=bond_data,
            send_email=False
        )
        submission_id = submission.get("submission_id")
        if not submission_id:
            print(f"❌ Failed to get submission_id: {submission}")
            sys.exit(1)
        print(f"✅ Submission created successfully! ID: {submission_id}")
    except Exception as e:
        print(f"❌ Error creating submission: {e}")
        sys.exit(1)

    # 2. Simulate webhook -> Download PDF and save to Google Drive
    print("🤖 Simulating webhook 'submission.completed'...")
    pdf_bytes = None
    try:
        pdf_bytes = await ds.download_combined_pdf(submission_id)
        if not pdf_bytes:
            print("❌ Downloaded PDF is empty.")
            sys.exit(1)
        print(f"✅ Downloaded combined PDF. Size: {len(pdf_bytes)} bytes")
    except Exception as e:
        print(f"❌ Error downloading PDF: {e}")
        sys.exit(1)

    print("📤 Pushing to Google Drive...")
    try:
        filed = ds.file_signed_pdf_to_drive(
            pdf_bytes,
            defendant_name=bond_data["defendant_name"],
            surety_id="osi",
            packet_id=packet_id,
            booking_number=bond_data["booking_number"],
        )
        if filed.get("ok"):
            print(f"✅ SUCCESS! File archived to Google Drive: {filed.get('drive_url')}")
        else:
            print(f"❌ Google Drive upload failed: {filed.get('error')}")
    except Exception as e:
        print(f"❌ Error filing to Google Drive: {e}")

if __name__ == "__main__":
    asyncio.run(main())
