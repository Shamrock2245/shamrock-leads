import asyncio
import os
import httpx
import sys

from services.signnow_packet_service import SignNowPacketService

async def main():
    service = SignNowPacketService()
    token = await service._get_token()
    
    missing_fields = set()
    found_fields = set()
    
    palmetto_keys = [
        "indemnity-agreement-palmetto",
        "defendant-application-palmetto",
        "surety-terms-palmetto",
        "collateral-receipt-palmetto",
        "payment-plan-palmetto"
    ]
    
    supported_keys = {
            "def_first", "def_middle", "def_last", "defendant_name", "DefendantName", "defendant-full-name", "DefName", "defendant-address", "DefCity", "DefState", "DefZip", "DefCounty", "DefHeight", "DefWeight", "DefRace", "DefHair", "DefEyes", "DefSex", "DefDL", "DefDLState", "DefEmployer", "DefEmpPhone", "DefEmpAddress", "indemnitor_name", "IndemnitorName", "indemnitor-full-name", "IndName", "IndAddress", "indemnitor-address", "indemnitor_address", "IndCityStateZip", "indemnitor_city", "indemnitor_state", "indemnitor_zip", "IndPhone", "indemnitor-phone", "indemnitor_phone", "Phone", "IndDL", "indemnitor_dl", "indemnitor_dl_state", "IndDOB", "indemnitor_dob", "IndSSN", "indemnitor-email", "indemnitor_email", "IndRelation", "IndEmployer", "IndEmpPhone", "IndEmpAddress", "IndCarMake", "IndCarModel", "IndCarYear", "IndCarColor", "Ref1Name", "Ref1Phone", "Ref1Relation", "Ref1Address", "Ref2Name", "Ref2Phone", "Ref2Relation", "Ref2Address", "FullName", "Social", "bond_amount", "BondAmount", "numeric-bond-amount", "NumericBondAmount", "premium_amount", "PremiumAmount", "premium-amount", "Premium", "booking_number", "BookingNumber", "arrest_number", "county", "arrest-county", "ArrestCounty", "facility", "JailFacility", "jail-facility", "charges", "ChargeDescription", "charge-description", "arrest-date", "ArrestDate", "case-number", "CaseNum", "CaseNumber", "court-date", "CourtDate", "court-time", "CourtTime", "court-location", "CourtLocation", "agent_name", "AgentName", "agent_license", "AgentLicense", "AgentLicenseNumber", "agency_name", "AgencyName", "agency_phone", "AgentPhone", "AgentAddress", "AgentCity", "AgentState", "AgentZip", "ReceiptNumber", "date", "Date", "DateSigned", "date-signed", "date-signed-ind", "date-signed-def", "date-signed-waiver", "DateDD", "Month", "YearYY", "intake_id"
    }

    async with httpx.AsyncClient() as client:
        for key in palmetto_keys:
            template_id = service.TEMPLATE_MAP.get(key)
            if not template_id:
                continue
                
            resp = await client.get(
                f"{service.base_url}/document/{template_id}",
                headers={"Authorization": f"Bearer {token}"}
            )
            data = resp.json()
            texts = data.get('texts', [])
            
            print(f"\nChecking Template: {key}")
            for t in texts:
                label = t.get('name', '') or t.get('label', '')
                if label not in supported_keys:
                    missing_fields.add(label)
                    print(f"  MISSING MAPPING: {label}")
                else:
                    found_fields.add(label)

    print("\n--- Summary ---")
    if missing_fields:
        print("The following Palmetto fields are NOT mapped in signnow_packet_service.py:")
        for f in sorted(missing_fields):
            print(f" - {f}")
    else:
        print("All Palmetto template fields are currently hydrated!")

asyncio.run(main())
