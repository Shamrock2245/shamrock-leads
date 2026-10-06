#!/usr/bin/env python3
"""Staff-directed Match + BondCase backfill for Schmidt booking 1033474.
Mirrors Fischer staff_direct pattern. Idempotent. Never prints secrets.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pymongo import MongoClient
from bson import ObjectId


BOOKING = "1033474"
PACKET_ID = "PKT-SCHMIDT-1033474"
CASE_NUMBER = "26CF017605"
SURETY = "osi"
POAS = [
    {
        "poa_number": "OSI-P6-116-26-0015",
        "statute": "893.13-6a",
        "description": "POSSESS CONTROLLED SUBSTANCE W/O PRESCRIPTION",
        "bond_amount": 5000.0,
    },
    {
        "poa_number": "OSI-P6-116-26-0016",
        "statute": "790.23-1a",
        "description": "POSSESSION OF WEAPON OR AMMO BY CONVICTED FLA FELON",
        "bond_amount": 5000.0,
    },
    {
        "poa_number": "OSI-P3-116-26-0018",
        "statute": "843.02",
        "description": "RESIST OFFICER (OBSTRUCT W/O VIOLENCE)",
        "bond_amount": 1000.0,
    },
    {
        "poa_number": "OSI-P3-116-26-0019",
        "statute": "893.147-1",
        "description": "DRUG EQUIP-POSSESS AND OR USE",
        "bond_amount": 1000.0,
    },
]
PRIMARY_POA = POAS[0]["poa_number"]
COURT_DATE = "2026-11-09"
COURT_TIME = "08:30"
COURT_LOCATION = "Circuit Court 1 EAST"
AGENCY = "FLORIDA HIGHWAY PATROL"


def load_env(env_path: Path) -> None:
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def ser(o):
    if isinstance(o, ObjectId):
        return str(o)
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, dict):
        return {k: ser(v) for k, v in o.items()}
    if isinstance(o, list):
        return [ser(x) for x in o]
    return o


def main() -> int:
    env_path = Path(
        os.environ.get(
            "SHAMROCK_LEADS_ENV",
            "/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/.env",
        )
    )
    if not env_path.exists():
        print(json.dumps({"ok": False, "error": f"env_missing:{env_path}"}))
        return 2
    load_env(env_path)
    uri = os.environ.get("MONGODB_URI") or os.environ.get("MONGO_URI")
    db_name = os.environ.get("MONGODB_DB_NAME") or "ShamrockBailDB"
    if not uri:
        print(json.dumps({"ok": False, "error": "MONGODB_URI_missing"}))
        return 2

    client = MongoClient(uri, serverSelectionTimeoutMS=12000)
    db = client.get_database(db_name)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    existing_bc = db.bond_cases.find_one(
        {
            "$or": [
                {"booking_number": BOOKING},
                {"Booking_Number": BOOKING},
                {"bond_case_id": BOOKING},
                {"Bond_Case_ID": BOOKING},
            ]
        }
    )
    existing_match = db.matches.find_one(
        {"$or": [{"booking_number": BOOKING}, {"arrest_booking_number": BOOKING}]}
    )
    packet = db.paperwork_packets.find_one({"packet_id": PACKET_ID})
    active = db.active_bonds.find_one({"booking_number": BOOKING})
    arrest = db.arrests.find_one({"booking_number": BOOKING})

    if not arrest:
        print(json.dumps({"ok": False, "error": "arrest_missing"}))
        return 1
    if not active:
        print(json.dumps({"ok": False, "error": "active_bond_missing"}))
        return 1
    if not packet:
        print(json.dumps({"ok": False, "error": "packet_missing"}))
        return 1

    if existing_bc and existing_match:
        # Still clear pending_staff_match / link packet if needed
        bc_id = existing_bc.get("bond_case_id") or existing_bc.get("Bond_Case_ID")
        mid = existing_match.get("match_id") or existing_match.get("Match_ID")
        did = existing_match.get("defendant_id") or existing_match.get("Defendant_ID")
        iid = existing_match.get("indemnitor_id") or existing_match.get("Indemnitor_ID")
        db.paperwork_packets.update_one(
            {"packet_id": PACKET_ID},
            {
                "$set": {
                    "pending_staff_match": False,
                    "bond_case_id": bc_id,
                    "match_id": mid,
                    "defendant_id": did,
                    "indemnitor_id": iid,
                    "booking_number": BOOKING,
                    "case_number": CASE_NUMBER,
                    "poa_number": PRIMARY_POA,
                    "updated_at": now_iso,
                    "staff_match_backfilled_at": now_iso,
                    "staff_match_backfill_source": "staff_direct_docuseal_schmidt",
                }
            },
        )
        db.active_bonds.update_one(
            {"booking_number": BOOKING},
            {
                "$set": {
                    "bond_case_id": bc_id,
                    "Bond_Case_ID": bc_id,
                    "match_id": mid,
                    "defendant_id": did,
                    "indemnitor_id": iid,
                    "poa_number": PRIMARY_POA,
                    "poa_numbers": [p["poa_number"] for p in POAS],
                    "insurance_company": "OSI",
                    "surety_id": SURETY,
                    "updated_at": now,
                }
            },
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "action": "already_exists_linked",
                    "bond_case_id": bc_id,
                    "match_id": mid,
                    "defendant_id": did,
                    "indemnitor_id": iid,
                    "packet_id": PACKET_ID,
                    "pending_staff_match": False,
                },
                indent=2,
            )
        )
        return 0

    if existing_bc or existing_match:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "partial_existing",
                    "bond_case": ser(existing_bc),
                    "match": ser(existing_match),
                },
                indent=2,
            )
        )
        return 1

    defendant_id = str(uuid.uuid4())
    indemnitor_id = str(uuid.uuid4())
    match_id = str(uuid.uuid4())
    bond_case_id = str(uuid.uuid4())

    first = (arrest.get("first_name") or "AARON").title()
    middle = (arrest.get("middle_name") or "PAUL").title()
    last = (arrest.get("last_name") or "SCHMIDT").title()
    full_name = f"{first} {middle} {last}".strip()
    dob = arrest.get("dob") or ""
    identity_key = f"schmidt:aaron:{dob or 'unknown'}"

    addr_raw = (arrest.get("address") or "").strip()
    # "3089 SUNRISE Trl PORT CHARLOTTE FL 33952"
    address = "3089 SUNRISE Trl"
    city = "PORT CHARLOTTE"
    state = "FL"
    zip_code = "33952"
    if addr_raw:
        # keep street as first part before city token if present
        if "PORT CHARLOTTE" in addr_raw.upper():
            address = addr_raw.upper().split("PORT CHARLOTTE")[0].strip().title()
            # fix Trl casing
            address = address.replace("Trl", "Trl").replace("Trail", "Trl")

    defendant_doc = {
        "identity_key": identity_key,
        "Booking_Number": BOOKING,
        "Defendant_ID": defendant_id,
        "defendant_id": defendant_id,
        "active": True,
        "address": address,
        "booking_number": BOOKING,
        "city": city,
        "counties": ["Lee"],
        "county": "Lee",
        "created_at": now_iso,
        "dob": dob,
        "email": f"aaron.schmidt.{BOOKING}@shamrockbailbonds.biz",
        "first_name": first,
        "first_seen": now_iso,
        "full_name": full_name,
        "height": str(arrest.get("height") or ""),
        "last_name": last,
        "last_seen": now_iso,
        "middle_name": middle,
        "race": arrest.get("race") or "",
        "sex": arrest.get("sex") or "",
        "source": "staff_direct_docuseal_schmidt",
        "state": state,
        "updated_at": now_iso,
        "weight": str(arrest.get("weight") or ""),
        "zip_code": zip_code,
        "esign_provider": "docuseal",
        "esign_provider_updated_at": now_iso,
    }

    # Indemnitor from active_bonds only — no invented phone
    ind = active.get("indemnitor") or {}
    ind_name = (active.get("indemnitor_name") or ind.get("name") or "Ruby Schmidt").strip()
    ind_email = (
        active.get("indemnitor_email")
        or ind.get("email")
        or "admin@shamrockbailbonds.biz"
    ).strip()
    ind_phone = (active.get("indemnitor_phone") or ind.get("phone") or "").strip()
    ind_first = (ind.get("firstName") or "Ruby").strip()
    ind_last = (ind.get("lastName") or "Schmidt").strip()

    indemnitor_doc = {
        "email": ind_email,
        "Indemnitor_ID": indemnitor_id,
        "indemnitor_id": indemnitor_id,
        "created_at": now_iso,
        "firstName": ind_first,
        "lastName": ind_last,
        "name": ind_name,
        "phone": ind_phone,
        "booking_number": BOOKING,
        "state": ind.get("state") or "FL",
        "dl_state": ind.get("dl_state") or "FL",
        "role": "primary",
        "notes": f"Staff-directed Match/BondCase backfill for booking {BOOKING}; email routed to office admin@",
        "source": "staff_direct_docuseal_schmidt",
        "updated_at": now_iso,
        "esign_provider": "docuseal",
        "esign_provider_updated_at": now_iso,
    }

    match_doc = {
        "booking_number": BOOKING,
        "county": "Lee",
        "Confidence": 100,
        "Defendant_ID": defendant_id,
        "Indemnitor_ID": indemnitor_id,
        "Match_ID": match_id,
        "Status": "validated",
        "arrest_booking_number": BOOKING,
        "confidence": 100,
        "confirmed_at": now_iso,
        "confirmed_by": "admin@shamrockbailbonds.biz",
        "created_at": now_iso,
        "defendant_id": defendant_id,
        "indemnitor_id": indemnitor_id,
        "match_id": match_id,
        "reason": (
            f"Staff-directed OSI packet backfill for {full_name}; "
            f"indemnitor {ind_name}; packet {PACKET_ID}"
        ),
        "status": "validated",
        "surety_id": SURETY,
        "updated_at": now_iso,
        "source": "staff_direct_docuseal_schmidt",
    }

    charge_details = []
    charge_bits = []
    for p in POAS:
        charge_details.append(
            {
                "description": p["description"],
                "statute": p["statute"],
                "bond_amount": p["bond_amount"],
                "case_number": CASE_NUMBER,
                "court": "Circuit Court",
                "hearing": f"{COURT_DATE} {COURT_TIME}",
                "agency": AGENCY,
                "poa_number": p["poa_number"],
            }
        )
        charge_bits.append(
            f"{p['statute']} {p['description']} ${int(p['bond_amount'])}"
        )

    bond_amount = float(active.get("bond_amount") or arrest.get("bond_amount") or 12000)
    premium = float(active.get("premium") or 1200)

    bond_case_doc = {
        "booking_number": BOOKING,
        "Bond_Amount": bond_amount,
        "Bond_Case_ID": bond_case_id,
        "Booking_Number": BOOKING,
        "Case_Number": CASE_NUMBER,
        "POA_Number": PRIMARY_POA,
        "Surety_ID": SURETY,
        "bond_amount": bond_amount,
        "bond_case_id": bond_case_id,
        "case_number": CASE_NUMBER,
        "charge_details": charge_details,
        "charges": " | ".join(charge_bits),
        "county": "Lee",
        "court_date": COURT_DATE,
        "court_location": COURT_LOCATION,
        "court_time": COURT_TIME,
        "created_at": now_iso,
        "defendant_id": defendant_id,
        "defendant_name": full_name,
        "indemnitor_id": indemnitor_id,
        "match_id": match_id,
        "packet_id": PACKET_ID,
        "poa_number": PRIMARY_POA,
        "poa_numbers": [p["poa_number"] for p in POAS],
        "premium": premium,
        "premium_amount": premium,
        "state": "FL",
        "status": "active",
        "surety_id": SURETY,
        "updated_at": now_iso,
        "source": "staff_direct_docuseal_schmidt",
        "agent_name": active.get("agent_name") or "admin@shamrockbailbonds.biz",
        "insurance_company": "OSI",
    }

    # Inserts
    db.defendants.insert_one(defendant_doc)
    db.indemnitors.insert_one(indemnitor_doc)
    db.matches.insert_one(match_doc)
    db.bond_cases.insert_one(bond_case_doc)

    db.active_bonds.update_one(
        {"booking_number": BOOKING},
        {
            "$set": {
                "bond_case_id": bond_case_id,
                "Bond_Case_ID": bond_case_id,
                "match_id": match_id,
                "defendant_id": defendant_id,
                "indemnitor_id": indemnitor_id,
                "poa_number": PRIMARY_POA,
                "poa_numbers": [p["poa_number"] for p in POAS],
                "insurance_company": "OSI",
                "surety_id": SURETY,
                "case_number": CASE_NUMBER,
                "updated_at": now,
            },
            "$push": {
                "status_history": {
                    "status": "match_bondcase_backfilled",
                    "timestamp": now_iso,
                    "agent": "Paperwork Desk",
                    "note": f"Staff-directed Match {match_id} + BondCase {bond_case_id}",
                }
            },
        },
    )

    db.paperwork_packets.update_one(
        {"packet_id": PACKET_ID},
        {
            "$set": {
                "pending_staff_match": False,
                "bond_case_id": bond_case_id,
                "match_id": match_id,
                "defendant_id": defendant_id,
                "indemnitor_id": indemnitor_id,
                "booking_number": BOOKING,
                "case_number": CASE_NUMBER,
                "poa_number": PRIMARY_POA,
                "surety_id": SURETY,
                "updated_at": now_iso,
                "staff_match_backfilled_at": now_iso,
                "staff_match_backfill_source": "staff_direct_docuseal_schmidt",
            }
        },
    )

    # Align POA inventory assigned_defendant where bond_case_id already set
    db.poa_inventory.update_many(
        {"bond_case_id": BOOKING},
        {
            "$set": {
                "assigned_defendant": full_name,
                "assigned_to": BOOKING,
                "updated_at": now_iso,
            }
        },
    )

    db.audit_events.insert_one(
        {
            "event_id": str(uuid.uuid4()),
            "event_type": "staff_direct_match_bondcase_backfill",
            "booking_number": BOOKING,
            "actor": "Paperwork Desk",
            "timestamp": now_iso,
            "details": {
                "defendant_id": defendant_id,
                "indemnitor_id": indemnitor_id,
                "match_id": match_id,
                "bond_case_id": bond_case_id,
                "packet_id": PACKET_ID,
                "poa_numbers": [p["poa_number"] for p in POAS],
                "case_number": CASE_NUMBER,
                "surety_id": SURETY,
                "pending_staff_match_cleared": True,
            },
        }
    )

    # Verify
    pkt2 = db.paperwork_packets.find_one(
        {"packet_id": PACKET_ID},
        {
            "packet_id": 1,
            "pending_staff_match": 1,
            "bond_case_id": 1,
            "match_id": 1,
            "defendant_id": 1,
            "indemnitor_id": 1,
            "booking_number": 1,
        },
    )
    print(
        json.dumps(
            {
                "ok": True,
                "action": "created",
                "defendant_id": defendant_id,
                "indemnitor_id": indemnitor_id,
                "match_id": match_id,
                "bond_case_id": bond_case_id,
                "packet_id": PACKET_ID,
                "packet_verify": ser(pkt2),
                "pending_staff_match": bool(pkt2 and pkt2.get("pending_staff_match")),
                "primary_poa": PRIMARY_POA,
                "case_number": CASE_NUMBER,
                "bond_amount": bond_amount,
                "premium": premium,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(json.dumps({"ok": False, "error": type(e).__name__, "message": str(e)[:500]}))
        raise SystemExit(1)
