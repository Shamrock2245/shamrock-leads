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
import tempfile
from datetime import datetime, timezone
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


E2E_DEFENDANT_NAME = "Marcus E2E Testcase"
E2E_INDEMNITOR_NAME = "Alicia E2E Cosigner"
E2E_COINDEMNITOR_NAME = "Jordan E2E Guarantor"
E2E_BONDSMAN_NAME = "Brendan O'Neal"


def _bond_data(surety_id: str) -> Dict[str, Any]:
    """Validated Match → BondCase mock with every signing role populated."""
    prefix = surety_id.upper()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    packet_key = f"e2e-{surety_id}-{stamp}-{os.getpid()}"
    poa = "OSI3 20139999" if surety_id == "osi" else "PSC5 17129999"
    defendant = {
        "name": E2E_DEFENDANT_NAME,
        "first_name": "Marcus",
        "last_name": "Testcase",
        "email": "admin+e2e-defendant@shamrockbailbonds.biz",
        "phone": "2395550101",
        "dob": "01/15/1994",
        "ssn": "000-00-0001",
        "dl": "T123456789012",
        "dl_state": "FL",
        "address": "100 Mock Jail St",
        "city": "Fort Myers",
        "state": "FL",
        "zip": "33901",
        "height": "5-10",
        "weight": "180",
        "hair": "BRO",
        "eyes": "BRN",
        "race": "W",
        "sex": "M",
        "employer": "Lee Health E2E",
        "employer_phone": "2395550110",
        "employer_address": "9981 HealthPark Cir, Fort Myers, FL 33908",
        "boss": "Dana Supervisor",
        "parent_name": "Pat Testcase",
        "parent_phone": "2395550111",
        "parent_address": "200 Mock Parent Ln, Fort Myers, FL 33901",
        "spouse_name": "Riley Testcase",
        "spouse_phone": "2395550112",
        "spouse_address": "100 Mock Jail St, Fort Myers, FL 33901",
        "spouse_employer": "Publix E2E",
        "best_friend_name": "Sam Neighbor",
        "best_friend_phone": "2395550113",
        "best_friend_address": "300 Mock Friend Rd, Cape Coral, FL 33904",
        "attorney_name": "Alex Counsel",
        "attorney_phone": "2395550114",
        "vehicle_year": "2016",
        "vehicle_make": "Honda",
        "vehicle_model": "Civic",
        "vehicle_color": "Silver",
        "vehicle_plate": "E2EMOCK",
    }
    indemnitor = {
        "name": E2E_INDEMNITOR_NAME,
        "first_name": "Alicia",
        "last_name": "Cosigner",
        "email": "admin+e2e-indemnitor@shamrockbailbonds.biz",
        "phone": "2395550202",
        "employer": "Publix Super Markets E2E",
        "city": "Cape Coral",
        "state": "FL",
        "zip": "33904",
        "vehicle_make": "Toyota",
        "vehicle_year": "2019",
        "vehicle_model": "Camry",
        "vehicle_color": "Blue",
        "ref1Name": "Chris Reference",
        "ref1Phone": "2395550210",
        "ref1Address": "12 Mock Ref Ave, Fort Myers, FL 33901",
        "ref1Relation": "Friend",
        "ref2Name": "Taylor Reference",
        "ref2Phone": "2395550211",
        "ref2Address": "14 Mock Ref Ave, Fort Myers, FL 33901",
        "ref2Relation": "Cousin",
        "spouse_name": "Morgan Cosigner",
        "spouse_phone": "2395550212",
        "spouse_employer": "Lee County Schools E2E",
    }
    coindemnitor = {
        "name": E2E_COINDEMNITOR_NAME,
        "first_name": "Jordan",
        "last_name": "Guarantor",
        "email": "admin+e2e-coindemnitor@shamrockbailbonds.biz",
        "phone": "2395550303",
        "employer": "Home Depot E2E",
        "city": "Lehigh Acres",
        "state": "FL",
        "zip": "33936",
    }
    return {
        "surety_id": surety_id,
        "match_status": "validated",
        "bond_case_id": f"BOND-{packet_key}",
        "match_id": f"MATCH-{packet_key}",
        "defendant_id": f"DEF-{packet_key}",
        "indemnitor_id": f"IND-{packet_key}",
        "packet_id": packet_key,
        "defendant_name": E2E_DEFENDANT_NAME,
        "indemnitor_name": E2E_INDEMNITOR_NAME,
        "indemnitor_email": indemnitor["email"],
        "indemnitor_phone": indemnitor["phone"],
        "defendant_email": defendant["email"],
        "defendant_phone": defendant["phone"],
        "county": "Lee",
        "case_number": f"26-CF-E2E-{prefix}",
        "poa_number": poa,
        "booking_number": f"E2E-{prefix}-BK-999",
        "bond_amount": 5000,
        "include_bondsman": True,
        "bondsman_name": E2E_BONDSMAN_NAME,
        "bondsman_email": "admin@shamrockbailbonds.biz",
        "bondsman_phone": "2393322245",
        "charge_details": [
            {
                "charge": f"TESTING E2E {prefix}",
                "bond_amount": 5000,
                "case_number": f"26-CF-E2E-{prefix}",
                "poa_number": poa,
            }
        ],
        "defendant": defendant,
        "indemnitor": indemnitor,
        "indemnitors": [indemnitor, coindemnitor],
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


REQUIRED_ROLES = {"bondsman", "indemnitor", "defendant", "coindemnitor"}
REQUIRED_PDF_MARKERS = (
    E2E_DEFENDANT_NAME,
    E2E_INDEMNITOR_NAME,
    E2E_COINDEMNITOR_NAME,
    E2E_BONDSMAN_NAME,
    "Lee",
)


def _pdf_text(pdf_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
        import io

        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        try:
            import pdfplumber
            import io

            with pdfplumber.open(io.BytesIO(pdf_bytes)) as doc:
                return "\n".join((page.extract_text() or "") for page in doc.pages)
        except Exception as e:
            return f"[pdf extract failed: {e}]"


async def _mock_sign_all_roles(ds, submission_id: Any) -> List[str]:
    """Mark every submitter completed (DocuSeal auto-signs mock signatures)."""
    raw = await ds.get_submission(submission_id)
    submitters = raw.get("submitters") or []
    completed_roles: List[str] = []
    for submitter in submitters:
        if not isinstance(submitter, dict):
            continue
        sid = submitter.get("id")
        role = str(submitter.get("role") or "").strip().lower()
        status = str(submitter.get("status") or "").lower()
        if not sid:
            continue
        if status in ("completed", "complete", "signed"):
            completed_roles.append(role)
            print(f"   already signed: {role}")
            continue
        await ds.update_submitter(sid, completed=True, send_email=False)
        completed_roles.append(role)
        print(f"   mock-signed: {role} (submitter {sid})")

    for _ in range(12):
        check = await ds.get_submission(submission_id)
        status = str(check.get("status") or "").lower()
        parties = check.get("submitters") or []
        done = [
            str(s.get("role") or "").lower()
            for s in parties
            if str(s.get("status") or "").lower() in ("completed", "complete", "signed")
        ]
        if status in ("completed", "complete") or set(done) >= REQUIRED_ROLES:
            return done
        await asyncio.sleep(1.5)
    return completed_roles


async def _run_docuseal_tab(ds, surety_id: str, *, archive: bool, mock_sign: bool) -> int:
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
    packet_id = bond_data["packet_id"]
    print(f"   template_id={template_id} packet_id={packet_id}")
    try:
        submission = await ds.create_submission_for_packet(
            template_id=template_id,
            packet_id=packet_id,
            bond_data=bond_data,
            indemnitors=bond_data["indemnitors"],
            defendant=bond_data["defendant"],
            send_email=False,
            include_defendant=True,
        )
    except Exception as e:
        print(f"   ❌ create submission failed: {e}")
        return 1

    submission_id = submission.get("submission_id")
    submitters = submission.get("submitters") or []
    roles = [str(s.get("role") or "").lower() for s in submitters if isinstance(s, dict)]
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
    missing_roles = REQUIRED_ROLES - set(roles)
    if missing_roles:
        print(f"   ❌ missing roles: {sorted(missing_roles)}")
        return 1
    if not links:
        print("   ❌ no sign links returned")
        return 1

    if mock_sign:
        print("   mock-signing all roles…")
        try:
            signed_roles = await _mock_sign_all_roles(ds, submission_id)
        except Exception as e:
            print(f"   ❌ mock-sign failed: {e}")
            return 1
        missing_signed = REQUIRED_ROLES - set(signed_roles)
        if missing_signed:
            print(f"   ❌ unsigned roles: {sorted(missing_signed)}")
            return 1
        print(f"   ✅ mock-signed {sorted(set(signed_roles))}")

    pdf_bytes = b""
    if mock_sign or archive:
        print("   downloading combined PDF…")
        try:
            pdf_bytes = await ds.download_combined_pdf(submission_id)
        except Exception as e:
            print(f"   ❌ PDF download failed: {e}")
            return 1
        if not pdf_bytes:
            print("   ❌ empty signed PDF")
            return 1
        print(f"   PDF {len(pdf_bytes)} bytes")
        local_pdf = os.path.join(
            tempfile.gettempdir(),
            f"{packet_id}.pdf",
        )
        with open(local_pdf, "wb") as handle:
            handle.write(pdf_bytes)
        print(f"   local copy: {local_pdf}")
        text = _pdf_text(pdf_bytes)
        missing_markers = [marker for marker in REQUIRED_PDF_MARKERS if marker.lower() not in text.lower()]
        if missing_markers:
            print(f"   ❌ PDF missing filled mock values: {missing_markers}")
            return 1
        print("   ✅ PDF contains defendant, indemnitor, co-indemnitor, bondsman, and county")

    if archive:
        if not pdf_bytes:
            print("   ❌ cannot archive — no PDF")
            return 2
        print("   filing to Drive Completed Bonds…")
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
        print(f"   ✅ Drive {filed.get('drive_url') or filed.get('webViewLink') or filed.get('id')}")
        if filed.get("path") or filed.get("folder_name"):
            print(f"   folder={filed.get('path') or filed.get('folder_name')}")
        if filed.get("filename"):
            print(f"   file={filed.get('filename')}")
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
        "--skip-sign",
        action="store_true",
        help="Do not auto-complete submitters (unsigned packet; Drive usually skipped)",
    )
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
    mock_sign = not args.skip_sign
    for sid in sureties:
        code = await _run_docuseal_tab(ds, sid, archive=archive, mock_sign=mock_sign)
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
