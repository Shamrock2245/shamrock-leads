"""
E2E Test: Paperwork Packet -> DocuSeal -> Google Drive

Usage (from shamrock-leads root):
    python scripts/e2e_test_paperwork.py
    python scripts/e2e_test_paperwork.py --skip-drive   # DocuSeal only
    python scripts/e2e_test_paperwork.py --drive-only   # preflight + skip live submit

Exit codes:
  0 success
  1 DocuSeal / general failure
  2 Drive auth / archive failure (paperwork otherwise OK)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

# Ensure imports work from project root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
load_dotenv()

# Prevent local execution from attempting to use Docker-internal DNS
if "DOCUSEAL_INTERNAL_URL" in os.environ:
    del os.environ["DOCUSEAL_INTERNAL_URL"]

# Prefer a real local SA path when Docker-style env points at a missing file
_sa_default = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "creds", "service-account-key.json")
)
_env_sa = (os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
if (not _env_sa or not os.path.isfile(_env_sa)) and os.path.isfile(_sa_default):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _sa_default


def _drive_preflight() -> dict:
    from dashboard.services.google_drive_service import GoogleDriveService

    drive = GoogleDriveService()
    result = drive.health_check()
    print("📁 Drive preflight:")
    print(f"   configured={result.get('configured')} auth_mode={result.get('auth_mode')}")
    print(f"   has_sa={result.get('has_service_account')} has_oauth={result.get('has_oauth')}")
    print(f"   folder_id={result.get('folder_id')} accessible={result.get('folder_accessible')}")
    if result.get("ok"):
        print(f"   ✅ Drive OK ({result.get('folder_name') or 'auth only'})")
    else:
        print(f"   ❌ Drive NOT OK: {result.get('error_code')}")
        if result.get("error"):
            print(f"   {result['error']}")
        if result.get("hint"):
            print(f"   Hint: {result['hint']}")
    return result


async def main() -> int:
    parser = argparse.ArgumentParser(description="E2E paperwork → DocuSeal → Drive")
    parser.add_argument("--skip-drive", action="store_true", help="Skip Drive archive step")
    parser.add_argument(
        "--drive-only",
        action="store_true",
        help="Only run Drive health preflight (no DocuSeal submission)",
    )
    args = parser.parse_args()

    print("🚀 Starting E2E Paperwork Test")

    # ── Drive preflight (fail early with actionable message) ───────────────
    drive_health = None
    if not args.skip_drive:
        drive_health = _drive_preflight()
        if args.drive_only:
            return 0 if drive_health.get("ok") else 2
        if not drive_health.get("ok"):
            print()
            print(
                "⚠️  Drive preflight failed — will still exercise DocuSeal, "
                "but archive step is expected to fail until Drive is fixed."
            )
            print("   Fix: python scripts/verify_drive_auth.py")
            print(
                "   Prefer SA: share Completed Bonds with "
                "bail-suite-sa@shamrock-bail-suite.iam.gserviceaccount.com as Editor"
            )
            print("   Or OAuth: python scripts/get_gmail_token.py  # includes Drive scope")
            print()

    from dashboard.services.docuseal_service import DocuSealService

    ds = DocuSealService()
    if not ds.is_configured:
        print("❌ DocuSeal is not configured. Check DOCUSEAL_API_KEY in .env")
        return 1

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
            {
                "charge": "TESTING E2E",
                "bond_amount": 1000,
                "case_number": "E2E-TEST-CASE",
                "poa_number": "OSI-TEST-1234",
            }
        ],
    }

    template_id = int(os.environ.get("DOCUSEAL_TEMPLATE_ID_OSI", 1))
    packet_id = "test-packet-e2e-123"

    print(f"📦 Generating submission for packet_id={packet_id}...")
    try:
        submission = await ds.create_submission_for_packet(
            template_id=template_id,
            packet_id=packet_id,
            bond_data=bond_data,
            send_email=False,
        )
        submission_id = submission.get("submission_id")
        if not submission_id:
            print(f"❌ Failed to get submission_id: {submission}")
            return 1
        print(f"✅ Submission created successfully! ID: {submission_id}")
    except Exception as e:
        print(f"❌ Error creating submission: {e}")
        return 1

    # 2. Simulate webhook → Download PDF
    print("🤖 Simulating webhook 'submission.completed'...")
    try:
        pdf_bytes = await ds.download_combined_pdf(submission_id)
        if not pdf_bytes:
            print("❌ Downloaded PDF is empty.")
            return 1
        print(f"✅ Downloaded combined PDF. Size: {len(pdf_bytes)} bytes")
    except Exception as e:
        print(f"❌ Error downloading PDF: {e}")
        return 1

    if args.skip_drive:
        print("⏭  Skipping Drive archive (--skip-drive)")
        return 0

    # 3. Archive to Completed Bonds
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
            print(f"   auth_mode={filed.get('auth_mode')} folder={filed.get('drive_folder_id')}")
            return 0

        print(f"❌ Google Drive upload failed: {filed.get('error')}")
        if filed.get("error_code"):
            print(f"   error_code={filed.get('error_code')}")
        if filed.get("hint"):
            print(f"   hint={filed.get('hint')}")
        print("   Repair: python scripts/verify_drive_auth.py")
        return 2
    except Exception as e:
        print(f"❌ Error filing to Google Drive: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
