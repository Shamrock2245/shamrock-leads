"""Fail-closed merge of bookmarklet booking extracts onto ArrestLeads."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from dashboard.services.booking_extract_merge import (
    BookingExtractError,
    charge_rows_from_payload,
    merge_booking_extract,
    merge_charge_details,
    names_agree,
    normalize_booking_extract,
    parse_name_parts,
)


def _bookmarklet_payload(**overrides):
    data = {
        "county": "Lee",
        "facility": "Lee County Jail",
        "defendantFullName": "PERKINS, MICHAEL JAMES",
        "defendantArrestNumber": "1029767",
        "bookingNumber": "1029767",
        "defendantDOB": "1985-04-12",
        "defendantRace": "W",
        "defendantSex": "M",
        "defendantHeight": "5'10\"",
        "defendantWeight": "180",
        "defendantStreetAddress": "2424 JACKSON ST",
        "defendantCity": "FORT MYERS",
        "defendantState": "FL",
        "defendantZip": "33901",
        "sourceUrl": "https://www.sheriffleefl.org/booking/1029767",
        "charges": [
            {
                "description": "DRUGS-POSSESS - POSSESS CONTROLLED SUBSTANCE W/O PRESCRIPTION",
                "bondAmount": "5000",
                "bondType": "CASH / SURETY",
                "caseNumber": "26CF016741",
                "hearing": "9/8/2026, 8:30:00 AM",
                "courtLocation": "LEE COUNTY JUSTICE CENTER",
            }
        ],
    }
    data.update(overrides)
    return data


class _Cursor:
    def __init__(self, items):
        self.items = items

    async def to_list(self, length=20):
        return deepcopy(self.items[:length])


class _Collection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.inserted = []
        self.updated = []

    def find(self, query):
        return _Cursor([doc for doc in self.docs if _matches(doc, query)])

    async def find_one(self, query):
        for doc in self.docs:
            if _matches(doc, query):
                return deepcopy(doc)
        return None

    async def insert_one(self, doc):
        stored = deepcopy(doc)
        stored.setdefault("_id", f"id-{len(self.docs) + 1}")
        self.docs.append(stored)
        self.inserted.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    async def update_one(self, query, update):
        for index, doc in enumerate(self.docs):
            if _matches(doc, query):
                self.docs[index].update(deepcopy(update.get("$set", {})))
                self.updated.append(self.docs[index])
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


def _matches(doc, query):
    if not isinstance(query, dict):
        return False
    if "$and" in query:
        return all(_matches(doc, clause) for clause in query["$and"])
    if "$or" in query:
        return any(_matches(doc, clause) for clause in query["$or"])
    if "$nin" in query:
        return False
    for key, expected in query.items():
        if key.startswith("$"):
            continue
        actual = doc.get(key)
        if isinstance(expected, dict) and "$regex" in expected:
            import re
            if actual is None:
                return False
            if not re.search(expected["$regex"], str(actual), re.I):
                return False
            continue
        if actual != expected:
            return False
    return True


@pytest.fixture
def collections():
    arrests = _Collection()
    active_bonds = _Collection()
    audit = _Collection()
    cols = {
        "arrests": arrests,
        "active_bonds": active_bonds,
        "audit_events": audit,
    }

    def _get(name):
        return cols[name]

    with patch("dashboard.services.booking_extract_merge.get_collection", side_effect=_get):
        yield cols


def test_login_redirect_preserves_defendants_deep_link():
    from dashboard.auth.pin_middleware import login_redirect_location, _LOGIN_HTML
    loc = login_redirect_location("/", "tab=defendants&write=1")
    assert loc.startswith("/login?next=")
    assert "tab%3Ddefendants" in loc or "tab=defendants" in loc
    assert "sl-booking-extract" in _LOGIN_HTML
    assert "sl_booking_extract" in _LOGIN_HTML
    assert login_redirect_location("/login") == "/login"
    assert login_redirect_location("//evil.example") == "/login?next=%2F"


def test_router_exposes_merge_endpoint():
    from dashboard.routers.booking_extract import router
    paths = [getattr(route, "path", "") for route in router.routes]
    assert "/api/leads/merge-booking-extract" in paths


def test_parse_name_parts_roster_format():
    first, last = parse_name_parts("PERKINS, MICHAEL JAMES")
    assert last == "PERKINS"
    assert first == "MICHAEL"


def test_names_agree_same_person_format_swap():
    assert names_agree("PERKINS, MICHAEL JAMES", "MICHAEL JAMES PERKINS") is True


def test_names_agree_rejects_different_last():
    assert names_agree("PERKINS, MICHAEL JAMES", "SMITH, MICHAEL JAMES") is False


def test_names_agree_empty_incoming_fails_closed():
    assert names_agree("PERKINS, MICHAEL", "") is False


def test_names_agree_empty_existing_allows_fill():
    assert names_agree("", "PERKINS, MICHAEL JAMES") is True


def test_normalize_extract_maps_bookmarklet_charges():
    extract = normalize_booking_extract(_bookmarklet_payload())
    assert extract["booking_number"] == "1029767"
    assert extract["county"] == "Lee"
    assert extract["full_name"] == "PERKINS, MICHAEL JAMES"
    assert extract["charge_details"][0]["case_number"] == "26CF016741"
    assert extract["charge_details"][0]["bond_amount"] == 5000
    assert extract["court_date"] == "9/8/2026"
    assert "8:30" in extract["court_time"]


def test_normalize_strips_booking_used_as_case():
    payload = _bookmarklet_payload(charges=[{
        "description": "BATTERY",
        "bondAmount": "1500",
        "caseNumber": "1029767",
        "hearing": "TBN",
    }])
    extract = normalize_booking_extract(payload)
    assert extract["charge_details"][0]["case_number"] == ""
    assert extract["case_number"] == ""


def test_normalize_requires_booking():
    with pytest.raises(BookingExtractError) as exc:
        normalize_booking_extract({"defendantFullName": "DOE, JOHN", "charges": []})
    assert exc.value.code == "missing_booking"


def test_charge_rows_from_payload_multi():
    rows = charge_rows_from_payload({
        "charges": [
            {"description": "A", "bondAmount": "1000", "caseNumber": "26CF1", "hearing": "9/1/2026, 8:00 AM"},
            {"description": "B", "bondAmount": "2000", "caseNumber": "26MM2"},
        ]
    }, "999")
    assert len(rows) == 2
    assert rows[0]["case_number"] == "26CF1"
    assert rows[1]["bond_amount"] == 2000


def test_merge_charge_details_preserves_poa():
    existing = [{
        "charge": "DRUGS-POSSESS - POSSESS CONTROLLED SUBSTANCE W/O PRESCRIPTION",
        "bond_amount": 0,
        "case_number": "",
        "poa_number": "OSI6 20132136",
    }]
    incoming = charge_rows_from_payload(_bookmarklet_payload(), "1029767")
    merged = merge_charge_details(existing, incoming, "1029767")
    assert merged[0]["poa_number"] == "OSI6 20132136"
    assert merged[0]["case_number"] == "26CF016741"
    assert merged[0]["bond_amount"] == 5000


@pytest.mark.asyncio
async def test_merge_creates_arrest_when_missing(collections):
    result = await merge_booking_extract(_bookmarklet_payload(), actor="brendan")
    assert result["created"] is True
    assert result["booking_number"] == "1029767"
    assert result["charge_count"] == 1
    assert result["total_bond"] == 5000
    assert collections["arrests"].inserted
    assert collections["audit_events"].inserted
    audit = collections["audit_events"].inserted[0]
    assert audit["event_type"] == "booking_extract_merge"
    assert "address" not in audit
    assert "dob" not in audit
    assert "full_name" not in audit


@pytest.mark.asyncio
async def test_merge_updates_existing_same_name(collections):
    collections["arrests"].docs.append({
        "_id": "a1",
        "booking_number": "1029767",
        "county": "Lee",
        "full_name": "PERKINS, MICHAEL JAMES",
        "bond_amount": 0,
        "charges": "DRUGS-POSSESS",
        "charge_details": [{
            "charge": "DRUGS-POSSESS - POSSESS CONTROLLED SUBSTANCE W/O PRESCRIPTION",
            "bond_amount": 0,
            "bond_type": "Surety",
            "case_number": "",
            "poa_number": "OSI6 20132136",
        }],
        "status": "In Custody",
    })
    result = await merge_booking_extract(_bookmarklet_payload())
    assert result["created"] is False
    updated = collections["arrests"].docs[0]
    assert updated["bond_amount"] == 5000
    assert updated["charge_details"][0]["case_number"] == "26CF016741"
    assert updated["charge_details"][0]["poa_number"] == "OSI6 20132136"
    assert result["lead"]["case_number"] == "26CF016741"


@pytest.mark.asyncio
async def test_merge_name_mismatch_fail_closed(collections):
    collections["arrests"].docs.append({
        "_id": "a1",
        "booking_number": "1029767",
        "county": "Lee",
        "full_name": "SMITH, JANE ANN",
        "bond_amount": 0,
    })
    with pytest.raises(BookingExtractError) as exc:
        await merge_booking_extract(_bookmarklet_payload())
    assert exc.value.code == "name_mismatch"
    assert collections["arrests"].updated == []


@pytest.mark.asyncio
async def test_merge_county_mismatch_fail_closed(collections):
    collections["arrests"].docs.append({
        "_id": "a1",
        "booking_number": "1029767",
        "county": "Collier",
        "full_name": "PERKINS, MICHAEL JAMES",
    })
    with pytest.raises(BookingExtractError) as exc:
        await merge_booking_extract(_bookmarklet_payload())
    assert exc.value.code == "county_mismatch"


@pytest.mark.asyncio
async def test_merge_does_not_clobber_existing_address(collections):
    collections["arrests"].docs.append({
        "_id": "a1",
        "booking_number": "1029767",
        "county": "Lee (FL)",
        "full_name": "PERKINS, MICHAEL JAMES",
        "address": "ON FILE ST",
        "dob": "1985-04-12",
        "bond_amount": 0,
        "charge_details": [],
        "status": "In Custody",
    })
    result = await merge_booking_extract(_bookmarklet_payload())
    assert result["created"] is False
    updated = collections["arrests"].docs[0]
    assert updated["address"] == "ON FILE ST"


@pytest.mark.asyncio
async def test_merge_missing_charges_fails(collections):
    with pytest.raises(BookingExtractError) as exc:
        await merge_booking_extract(_bookmarklet_payload(charges=[]))
    assert exc.value.code == "missing_charges"
