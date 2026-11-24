"""Contract tests for Shannon live MongoDB voice inmate & release lookup."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from dashboard.main import app

client = TestClient(app)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    async def to_list(self, length=None):
        return self._rows[:length] if length is not None else list(self._rows)


class _FakeCollection:
    def __init__(self, rows=None, one=None):
        self._rows = list(rows or [])
        self._one = one
        self.last_find_query = None

    def find(self, query, _projection=None):
        self.last_find_query = query
        return _FakeCursor(self._rows)

    async def find_one(self, _query, _projection=None):
        return self._one


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PIN", "synthetic-test-pin-0000")
    monkeypatch.setenv("SECRET_KEY", "synthetic-test-secret-key-not-real")
    monkeypatch.setenv("ELEVENLABS_TOOL_SECRET", "synthetic-el-voice-secret-123")
    monkeypatch.delenv("GAS_API_KEY", raising=False)
    monkeypatch.delenv("LEADS_INTERNAL_TOKEN", raising=False)


def _wire_collections(monkeypatch, *, arrests=None, active_bonds=None, court_reminders=None):
    cols = {
        "arrests": _FakeCollection(rows=arrests or []),
        "active_bonds": _FakeCollection(one=active_bonds),
        "court_reminders": _FakeCollection(one=court_reminders),
    }

    def fake_get_collection(name):
        return cols.get(name, _FakeCollection())

    monkeypatch.setattr("dashboard.routers.shannon_voice_lookup.get_collection", fake_get_collection)
    return cols


def test_voice_inmate_lookup_fails_closed_without_auth():
    r_post = client.post(
        "/api/paperwork/shannon/inmate-status",
        json={"defendant_name": "Jane Doe", "county": "Lee"},
    )
    assert r_post.status_code == 401
    r_get = client.get("/api/paperwork/shannon/inmate-status?defendant_name=Jane+Doe&county=Lee")
    assert r_get.status_code == 401
    r_bad = client.post(
        "/api/paperwork/shannon/inmate-status",
        headers={"X-API-Key": "wrong-secret"},
        json={"defendant_name": "Jane Doe", "county": "Lee"},
    )
    assert r_bad.status_code == 401


def test_voice_inmate_lookup_bond_set_returns_estimate_and_release_guidance(monkeypatch):
    _wire_collections(
        monkeypatch,
        arrests=[
            {
                "full_name": "DOE, JANE MARIE",
                "booking_number": "26-009911",
                "county": "Lee",
                "state": "FL",
                "custody_status": "In Custody",
                "bond_amount": 5000,
                "bond_type": "Surety",
                "charges": ["GRAND THEFT", "RESISTING OFFICER"],
                "booking_date": "2026-11-24",
            }
        ],
    )
    resp = client.get(
        "/api/paperwork/shannon/inmate-status",
        params={"defendant_name": "Jane Doe", "county": "Lee"},
        headers={"X-API-Key": "synthetic-el-voice-secret-123"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    assert data["status"] == "bond_set"
    assert data["can_start_intake"] is True
    assert data["defendant"]["county"] == "Lee"
    assert data["defendant"]["bond_amount"] == 5000.0
    assert data["premium_estimate"]["estimated_premium"] == 500.0
    assert data["premium_estimate"]["transfer_fee"] == 0.0
    assert data["release_guidance"]["never_promise_exact_time"] is True
    assert "2 to 6 hours" in data["message"]
    assert "never guarantee an exact release time" in data["message"].lower()


def test_voice_inmate_lookup_detects_dui_and_dv_holds(monkeypatch):
    _wire_collections(
        monkeypatch,
        arrests=[
            {
                "full_name": "SMITH, JOHN",
                "booking_number": "26-12345",
                "county": "Collier",
                "state": "FL",
                "custody_status": "In Custody",
                "bond_amount": 0,
                "bond_type": "No Bond",
                "charges": "DUI FIRST OFFENSE; BATTERY DOMESTIC VIOLENCE",
            }
        ],
    )
    resp = client.post(
        "/api/paperwork/shannon/inmate-status",
        headers={"Authorization": "Bearer synthetic-el-voice-secret-123"},
        json={"defendant_name": "John Smith", "county": "Collier"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    assert data["status"] == "bond_not_set"
    notes = " ".join(data["release_guidance"]["special_hold_notes"]).lower()
    assert "8-hour" in notes
    assert "domestic violence" in notes
    assert "first appearance" in data["message"].lower()


def test_voice_inmate_lookup_released_defendant_is_honest(monkeypatch):
    _wire_collections(
        monkeypatch,
        arrests=[
            {
                "full_name": "JONES, ALEX",
                "booking_number": "26-77777",
                "county": "Charlotte",
                "state": "FL",
                "custody_status": "Released",
                "bond_amount": 2500,
                "charges": "TRESPASS",
            }
        ],
    )
    resp = client.post(
        "/api/paperwork/shannon/inmate-status",
        headers={"X-API-Key": "synthetic-el-voice-secret-123"},
        json={"defendant_name": "Alex Jones", "county": "Charlotte"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    assert data["status"] == "released"
    assert data["can_start_intake"] is False
    assert "already released" in data["message"].lower()


def test_voice_inmate_lookup_ambiguous_fails_closed(monkeypatch):
    _wire_collections(
        monkeypatch,
        arrests=[
            {"full_name": "BROWN, MICHAEL A", "county": "Lee", "booking_date": "2026-11-24"},
            {"full_name": "BROWN, MICHAEL B", "county": "Collier", "booking_date": "2026-11-23"},
        ],
    )
    resp = client.post(
        "/api/paperwork/shannon/inmate-status",
        headers={"X-API-Key": "synthetic-el-voice-secret-123"},
        json={"defendant_name": "Michael Brown"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is False
    assert data["ambiguous"] is True
    assert data["status"] == "multiple_matches"
    assert len(data["candidates"]) == 2


def test_voice_inmate_lookup_active_bond_never_leaks_third_party_pii(monkeypatch):
    _wire_collections(
        monkeypatch,
        arrests=[],
        active_bonds={
            "status": "active",
            "county": "Lee",
            "defendant_name": "TAYLOR, SAM",
            "case_number": "26-CF-001122",
            "bond_amount": 10000,
            "next_court_date": "2026-12-15 08:30 AM",
            # Sensitive third-party PII fields that must NEVER appear in response:
            "indemnitor_name": "Secret Cosigner",
            "indemnitor_phone": "+12395559999",
            "indemnitor_email": "cosigner@private.test",
            "indemnitor_ssn": "123-45-6789",
        },
    )
    resp = client.post(
        "/api/paperwork/shannon/inmate-status",
        headers={"X-API-Key": "synthetic-el-voice-secret-123"},
        json={"defendant_name": "Sam Taylor", "county": "Lee"},
    )
    assert resp.status_code == 200
    raw = resp.text
    assert "Secret Cosigner" not in raw
    assert "2395559999" not in raw
    assert "cosigner@private.test" not in raw
    assert "123-45-6789" not in raw
    data = resp.json()
    assert data["found"] is True
    assert data["status"] == "active_bond_on_file"
    assert "2026-12-15" in data["message"]


def test_voice_inmate_lookup_not_found_still_enables_intake(monkeypatch):
    _wire_collections(monkeypatch, arrests=[])
    resp = client.post(
        "/api/paperwork/shannon/inmate-status",
        headers={"X-API-Key": "synthetic-el-voice-secret-123"},
        json={"defendant_name": "Nobody Listed", "county": "Sarasota"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is False
    assert data["status"] == "not_in_system"
    assert data["can_start_intake"] is True
    assert data["county_info"]["county"] == "Sarasota"
    assert "1:30 PM" in data["release_guidance"]["first_appearance_schedule"]


def test_voice_inmate_lookup_redacts_miami_dade_internal_key(monkeypatch):
    internal_key = "md_dedupe_v2:" + ("a" * 64)
    _wire_collections(
        monkeypatch,
        arrests=[
            {
                "full_name": "PEREZ, CARLOS",
                "booking_number": internal_key,
                "county": "Miami-Dade",
                "state": "FL",
                "custody_status": "In Custody",
                "bond_amount": 3000,
                "charges": "PETIT THEFT",
            }
        ],
    )
    resp = client.post(
        "/api/paperwork/shannon/inmate-status",
        headers={"X-API-Key": "synthetic-el-voice-secret-123"},
        json={"defendant_name": "Carlos Perez", "county": "Miami-Dade"},
    )
    assert resp.status_code == 200
    assert internal_key not in resp.text
    assert "md_dedupe_v2" not in resp.text
    data = resp.json()
    assert data["found"] is True
    assert data["defendant"]["booking_number"] == ""
    assert data["premium_estimate"]["transfer_fee"] == 100.0
