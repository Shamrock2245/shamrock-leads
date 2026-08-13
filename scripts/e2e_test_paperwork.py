"""
E2E Test: Paperwork tabs (OSI + Palmetto) → DocuSeal → optional Drive

Covers the dashboard Paperwork Config surety tabs:
  - local PDF packet composition (osi / palmetto folders)
  - DocuSeal template resolution (no Palmetto→OSI fallback)
  - live unsigned multi-party submission + sign links

Usage (from shamrock-leads root):
    python scripts/e2e_test_paperwork.py
    python scripts/e2e_test_paperwork.py --surety both
    python scripts/e2e_test_paperwork.py --surety osi --skip-drive
    python scripts/e2e_test_paperwork.py --surety palmetto --skip-drive
    python scripts/e2e_test_paperwork.py --drive-only

Exit codes:
  0 success (every requested surety tab passed)
  1 DocuSeal / tab / general failure
  2 Drive auth / archive failure (paperwork otherwise OK)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# Ensure imports work from project root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
_ENV_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
load_dotenv(_ENV_PATH)

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


def _bond_data(surety_id: str) -> Dict[str, Any]:
    prefix = surety_id.upper()
    return {
        "surety_id": surety_id,
        "defendant_name": f"E2E {prefix} Defendant",
        "indemnitor_name": f"E2E {prefix} Indemnitor",
        "indemnitor_email": "admin@shamrockbailbonds.biz",
        "defendant_email": "admin@shamrockbailbonds.biz",
        "county": "Lee",
        "case_number": f"E2E-{prefix}-CASE",
        "poa_number": f"{prefix}-TEST-1234",
        "booking_number": f"E2E-{prefix}-BK-999",
        "bond_amount": 1000,
        "charge_details": [
            {
                "charge": f"TESTING E2E {prefix}",
                "bond_amount": 1000,
                "case_number": f"E2E-{prefix}-CASE",
                "poa_number": f"{prefix}-TEST-1234",
            }
        ],
    }


def _run_tab_composition(surety_id: str) -> bool:
    """Exercise the Paperwork Config surety tab (local blanks + composition rule)."""
    from dashboard.paperwork_pdf_service import (
        PACKET_DOC_ORDER,
        list_available_blanks,
        packet_composition,
    )

    print(f"\n── Paperwork tab: {surety_id.upper()} ──")
    comp = packet_composition(surety_id)
    expected = (
        "surety-agnostic-shamrock + palmetto"
        if surety_id == "palmetto"
        else "surety-agnostic-shamrock + osi"
    )
    print(f"   composition: {comp.get('rule')}")
    if expected not in (comp.get("rule") or ""):
        print(f"   ❌ unexpected composition rule (want {expected})")
        return False

    blanks = list_available_blanks(surety_id)
    missing = [slug for slug in PACKET_DOC_ORDER if not blanks.get(slug)]
    print_only_ok = blanks.get("appearance-bond") is True
    print(f"   packet blanks: {sum(1 for s in PACKET_DOC_ORDER if blanks.get(s))}/{len(PACKET_DOC_ORDER)}")
    print(f"   appearance bond (print-only): {'yes' if print_only_ok else 'MISSING'}")
    if missing:
        print(f"   ❌ missing blanks: {', '.join(missing)}")
        return False
    if not print_only_ok:
        print("   ❌ appearance-bond blank missing")
        return False
    print(f"   ✅ {surety_id.upper()} tab local packet is complete")
    return True


def _summarize_templates(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = raw.get("data") or raw.get("templates") or []
    else:
        items = []
    out: List[Dict[str, Any]] = []
    for t in items:
        if isinstance(t, dict):
            out.append(
                {
                    "id": t.get("id"),
                    "name": t.get("name") or t.get("slug") or "",
                    "archived": t.get("archived"),
                }
            )
    return out


async def _run_docuseal_tab(ds, surety_id: str, *, archive: bool) -> int:
    """
    Returns 0 ok, 1 tab/DocuSeal fail, 2 Drive fail after a good submission.
    """
    from dashboard.services.docuseal_service import resolve_template_id_for_surety

    template_id = resolve_template_id_for_surety(surety_id)
    print(f"\n── DocuSeal submit: {surety_id.upper()} ──")
    if not template_id:
        print(
            f"   ❌ no template id for {surety_id} "
            f"(set DOCUSEAL_TEMPLATE_ID_{surety_id.upper()} — Palmetto must not fall back to OSI)"
        )
        return 1

    bond_data = _bond_data(surety_id)
    packet_id = f"e2e-{surety_id}-{os.getpid()}"
    print(f"   template_id={template_id} packet_id={packet_id}")
    try:
        submission = await ds.create_submission_for_packet(
            template_id=template_id,
            packet_id=packet_id,
            bond_data=bond_data,
            send_email=False,
            include_defendant=True,
        )
    except Exception as e:
        print(f"   ❌ create submission failed: {e}")
        return 1

    submission_id = submission.get("submission_id")
    submitters = submission.get("submitters") or []
    roles = [str(s.get("role") or "") for s in submitters if isinstance(s, dict)]
    links = [
        s.get("sign_url")
        for s in submitters
        if isinstance(s, dict) and s.get("sign_url")
    ]
    if not submission_id:
        print(f"   ❌ no submission_id: {submission}")
        return 1
    print(f"   ✅ submission {submission_id}")
    print(f"   roles={roles or ['(none)']}")
    print(f"   sign_links={len(links)}")
    if not links:
        print("   ❌ no sign links returned")
        return 1

    # Unsigned packets often have no merged PDF yet — that is not a tab failure.
    if archive:
        print("   downloading combined PDF for Drive archive…")
        try:
            pdf_bytes = await ds.download_combined_pdf(submission_id)
        except Exception as e:
            print(f"   ⚠️  PDF download skipped (unsigned?): {e}")
            pdf_bytes = b""
        if not pdf_bytes:
            print("   ⚠️  no signed PDF yet — skipping Drive (submission still OK)")
            return 0
        print(f"   PDF {len(pdf_bytes)} bytes — filing to Drive…")
        try:
            filed = ds.file_signed_pdf_to_drive(
                pdf_bytes,
                defendant_name=bond_data["defendant_name"],
                surety_id=surety_id,
                packet_id=packet_id,
                booking_number=bond_data["booking_number"],
            )
        except Exception as e:
            print(f"   ❌ Drive exception: {e}")
            return 2
        if not filed.get("ok"):
            print(f"   ❌ Drive upload failed: {filed.get('error')}")
            if filed.get("hint"):
                print(f"   hint: {filed.get('hint')}")
            return 2
        print(f"   ✅ Drive {filed.get('drive_url')}")
    else:
        print("   ⏭  Drive archive skipped")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description="E2E paperwork tabs → DocuSeal → Drive")
    parser.add_argument(
        "--surety",
        choices=["osi", "palmetto", "both"],
        default="both",
        help="Which paperwork tab(s) to exercise (default both)",
    )
    parser.add_argument("--skip-drive", action="store_true", help="Skip Drive archive step")
    parser.add_argument(
        "--drive-only",
        action="store_true",
        help="Only run Drive health preflight (no DocuSeal submission)",
    )
    args = parser.parse_args()

    sureties = ["osi", "palmetto"] if args.surety == "both" else [args.surety]
    print("🚀 Starting E2E Paperwork Test")
    print(f"   tabs: {', '.join(s.upper() for s in sureties)}")

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
            print()

    tab_ok = True
    for sid in sureties:
        if not _run_tab_composition(sid):
            tab_ok = False
    if not tab_ok:
        print("\n❌ Paperwork tab composition failed")
        return 1

    from dashboard.services.docuseal_service import DocuSealService

    ds = DocuSealService()
    if not ds.is_configured:
        print("❌ DocuSeal is not configured. Check DOCUSEAL_API_KEY in .env")
        return 1
    print("\n✅ DocuSeal service initialized.")

    try:
        raw = await ds.list_templates()
        templates = _summarize_templates(raw)
        print(f"   live templates: {len(templates)}")
        for t in templates[:20]:
            print(f"     id={t.get('id')} name={t.get('name')!r}")
    except Exception as e:
        print(f"   ⚠️  template list failed: {e}")

    worst = 0
    archive = not args.skip_drive
    for sid in sureties:
        code = await _run_docuseal_tab(ds, sid, archive=archive)
        worst = max(worst, code)

    if worst == 0:
        print("\n✅ E2E paperwork tabs passed:", ", ".join(s.upper() for s in sureties))
    elif worst == 2:
        print("\n⚠️  Paperwork tabs submitted; Drive archive failed")
    else:
        print("\n❌ E2E paperwork tab failure")
    return worst


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
