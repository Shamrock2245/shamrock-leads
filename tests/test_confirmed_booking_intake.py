from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from dashboard.services.confirmed_booking_intake import (
    BookingIntakeError,
    build_preview,
    confirm_preview,
    normalize_lee_booking_url,
    project_public_booking_facts,
)


class _Cursor:
    def __init__(self, items):
        self.items = items

    async def to_list(self, length=20):
        return deepcopy(self.items[:length])


class _Collection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.indexes = []

    async def create_index(self, *args, **kwargs):
        self.indexes.append((args, kwargs))
        return kwargs.get("name", "idx")

    async def insert_one(self, doc):
        stored = deepcopy(doc)
        stored.setdefault("_id", f"id-{len(self.docs) + 1}")
        self.docs.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    async def find_one(self, query):
        for doc in self.docs:
            if all(_matches(doc, key, value) for key, value in query.items()):
                return deepcopy(doc)
        return None

    def find(self, query):
        return _Cursor([doc for doc in self.docs if all(_matches(doc, key, value) for key, value in query.items())])

    async def update_one(self, query, update, upsert=False):
        for index, doc in enumerate(self.docs):
            if all(_matches(doc, key, value) for key, value in query.items()):
                self.docs[index].update(deepcopy(update.get("$set", {})))
                return SimpleNamespace(matched_count=1, upserted_id=None)
        if upsert:
            doc = deepcopy(query)
            doc.update(deepcopy(update.get("$setOnInsert", {})))
            doc.update(deepcopy(update.get("$set", {})))
            doc.setdefault("_id", f"id-{len(self.docs) + 1}")
            self.docs.append(doc)
            return SimpleNamespace(matched_count=0, upserted_id=doc["_id"])
        return SimpleNamespace(matched_count=0, upserted_id=None)


def _matches(doc, key, expected):
    if key == "$or":
        return any(all(_matches(doc, k, v) for k, v in clause.items()) for clause in expected)
    actual = doc.get(key)
    if isinstance(expected, dict) and "$gt" in expected:
        return actual is not None and actual > expected["$gt"]
    return actual == expected


def _parsed_booking():
    return {
        "booking_number": "1030773",
        "full_name": "Local Booking Fixture",
        "county": "Lee",
        "facility": "Lee County Jail",
        "status": "In Custody",
        "charges": "Fixture charge",
        "charge_details": [{
            "charge": "Fixture charge",
            "bond_amount": 2500,
            "bond_type": "Surety",
            "case_number": "CASE-1",
        }],
        "bond_amount": "2500",
        "case_number": "CASE-1",
        "court_date": "2026-08-21",
        "court_time": "09:00:00",
        "court_location": "Fixture Court",
        # Deliberately supplied: the projection must strip all of these.
        "address": "100 Do Not Persist Way",
        "defendant_address": "100 Do Not Persist Way",
        "dob": "1980-01-01",
        "date_of_birth": "01/01/1980",
        "phone": "2395550199",
        "email": "fixture@example.com",
    }


def test_only_supported_lee_booking_urls_are_accepted():
    url, booking_id = normalize_lee_booking_url("https://www.sheriffleefl.org/booking/?id=1030773")
    assert url.endswith("id=1030773")
    assert booking_id == "1030773"

    for bad in (
        "http://www.sheriffleefl.org/booking/?id=1030773",
        "https://example.com/booking/?id=1030773",
        "https://127.0.0.1/booking/?id=1030773",
        "https://www.sheriffleefl.org/booking/?id=one",
        "https://www.sheriffleefl.org/booking/?id=1&id=2",
    ):
        with pytest.raises(BookingIntakeError):
            normalize_lee_booking_url(bad)


def test_projection_excludes_address_contact_dob_and_raw_data():
    preview = project_public_booking_facts(
        _parsed_booking(),
        booking_id="1030773",
        source_url="https://www.sheriffleefl.org/booking/?id=1030773",
        parse_method="lee_county_api",
    )
    assert preview["booking_dedup_key"] == "FL|LEE|1030773"
    assert preview["charge_details"][0]["charge"] == "Fixture charge"
    forbidden = {"address", "defendant_address", "dob", "date_of_birth", "phone", "email", "raw", "html"}
    assert not (forbidden & set(preview))
    assert "Do Not Persist" not in repr(preview)
    assert "fixture@example.com" not in repr(preview)


@pytest.mark.asyncio
async def test_build_preview_is_minimized_and_expiring():
    previews = _Collection()
    with patch(
        "dashboard.services.url_ingest_service.ingest_url",
        new=AsyncMock(return_value={"success": True, "data": _parsed_booking(), "parse_method": "lee_county_api"}),
    ):
        result = await build_preview("https://www.sheriffleefl.org/booking/?id=1030773", previews)

    assert result["preview"]["booking_number"] == "1030773"
    assert "address" not in result["preview"]
    assert "dob" not in result["preview"]
    assert len(previews.docs) == 1
    assert previews.docs[0]["expires_at"] > previews.docs[0]["created_at"]


@pytest.mark.asyncio
async def test_confirm_requires_exact_acknowledged_unexpired_preview_and_creates_arrest_lead():
    previews = _Collection([{
        "_id": "preview-1",
        "preview_id": "bip_test_preview",
        "facts": project_public_booking_facts(_parsed_booking(), booking_id="1030773", source_url="https://www.sheriffleefl.org/booking/?id=1030773", parse_method="lee_county_api"),
        "expires_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc) + timedelta(minutes=10),
        "consumed_at": None,
    }])
    arrests, audits = _Collection(), _Collection()

    with pytest.raises(BookingIntakeError):
        await confirm_preview(
            preview_id="bip_test_preview", confirmed_booking_number="1030773", exact_match_confirmed=False,
            preview_collection=previews, arrests_collection=arrests, audit_collection=audits,
        )

    result = await confirm_preview(
        preview_id="bip_test_preview", confirmed_booking_number="1030773", exact_match_confirmed=True,
        preview_collection=previews, arrests_collection=arrests, audit_collection=audits,
    )
    assert result["success"] is True
    assert result["outcome"] == "created"
    assert arrests.docs[0]["booking_dedup_key"] == "FL|LEE|1030773"
    assert "address" not in arrests.docs[0]
    assert previews.docs[0]["consumed_at"] is not None
    assert audits.docs[0]["event_type"] == "booking_url_intake_created"


@pytest.mark.asyncio
async def test_cross_jurisdiction_booking_conflict_requires_staff_review_without_mutation():
    previews = _Collection([{
        "_id": "preview-2",
        "preview_id": "bip_cross_state",
        "facts": project_public_booking_facts(_parsed_booking(), booking_id="1030773", source_url="https://www.sheriffleefl.org/booking/?id=1030773", parse_method="lee_county_api"),
        "expires_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc) + timedelta(minutes=10),
        "consumed_at": None,
    }])
    arrests = _Collection([{"_id": "sc-lee", "booking_number": "1030773", "county": "Lee", "state": "SC", "full_name": "Existing"}])
    audits = _Collection()

    result = await confirm_preview(
        preview_id="bip_cross_state", confirmed_booking_number="1030773", exact_match_confirmed=True,
        preview_collection=previews, arrests_collection=arrests, audit_collection=audits,
    )
    assert result["success"] is False
    assert result["outcome"] == "requires_staff_review"
    assert len(arrests.docs) == 1
    assert previews.docs[0]["consumed_at"] is None
