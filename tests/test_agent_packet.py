"""Tests for chat-agent packet staging API (/api/agent/stage-packet & /api/agent/parse-booking-url)."""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.routers.agent_packet import router as agent_packet_router
from tests.bb_fake_mongo import FakeCollection


def _make_client():
    app = FastAPI()
    app.include_router(agent_packet_router)
    return TestClient(app)


def test_stage_packet_requires_auth(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    monkeypatch.delenv("LEADS_INTERNAL_TOKEN", raising=False)
    client = _make_client()

    resp = client.post("/api/agent/stage-packet", json={})
    assert resp.status_code == 401

    resp_bad = client.post(
        "/api/agent/stage-packet",
        headers={"X-API-Key": "wrong"},
        json={},
    )
    assert resp_bad.status_code == 401


def test_stage_packet_blocks_ssrf_urls(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()

    for bad_url in (
        "http://127.0.0.1:8000/secret",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost/admin",
        "file:///etc/passwd",
    ):
        resp = client.post(
            "/api/agent/stage-packet",
            headers={"X-API-Key": "secret-gas-key"},
            json={"booking_url": bad_url},
        )
        assert resp.status_code == 400, f"Expected 400 for {bad_url}, got {resp.status_code}"


def test_stage_packet_merges_booking_url_id_ocr_and_chat_input(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    monkeypatch.setenv("SUPER_CRM_URL", "https://leads.shamrockbailbonds.biz")
    client = _make_client()

    cols = {
        "arrests": FakeCollection("arrests"),
        "intake_queue": FakeCollection("intake_queue"),
        "paperwork_packets": FakeCollection("paperwork_packets"),
    }

    fake_booking = {
        "success": True,
        "parse_method": "lee_api",
        "data": {
            "full_name": "JANE MARIE DOE",
            "first_name": "Jane",
            "last_name": "Doe",
            "booking_number": "26-009911",
            "county": "Lee",
            "state": "FL",
            "dob": "1992-04-12",
            "total_bond": 5000.0,
            "charges": [
                {
                    "charge": "GRAND THEFT >$750 <$5,000",
                    "bond_amount": 5000.0,
                    "case_number": "26-CF-001122",
                }
            ],
            "case_number": "26-CF-001122",
        },
    }
    fake_ocr = {
        "success": True,
        "data": {
            "full_name": "JOHN ROBERT SMITH",
            "first_name": "JOHN",
            "last_name": "SMITH",
            "dob": "1978-09-01",
            "dl_number": "S530436783210",
            "dl_state": "FL",
            "address": "1528 BROADWAY",
            "city": "FORT MYERS",
            "state": "FL",
            "zip": "33901",
        },
    }

    fake_img_b64 = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xd9").decode()

    with patch("dashboard.routers.agent_packet.get_collection", side_effect=lambda name: cols[name]), \
         patch("dashboard.routers.agent_packet.ingest_url", AsyncMock(return_value=fake_booking)), \
         patch("dashboard.routers.agent_packet.IDScannerService.scan_id_image", AsyncMock(return_value=fake_ocr)):
        resp = client.post(
            "/api/agent/stage-packet",
            headers={"Authorization": "Bearer secret-gas-key"},
            json={
                "booking_url": "https://www.sheriffleefl.org/booking/?id=26009911",
                "indemnitor": {
                    "phone": "2395550199",
                    "email": "john@example.com",
                    "relationship": "Spouse",
                },
                "id_image_base64": fake_img_b64,
                "surety": "palmetto",
                "notes": "Staged from chat",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["status"] == "staged_ready_for_staff"
    assert data["packet_id"].startswith("PKT-")
    assert data["intake_id"].startswith("INT-AGT-")
    assert data["super_crm_url"] == f"https://leads.shamrockbailbonds.biz/?tab=paperwork&packet_id={data['packet_id']}"

    # Verbatim charge & bond preserved
    assert data["defendant"]["full_name"] == "JANE MARIE DOE"
    assert data["defendant"]["booking_number"] == "26-009911"
    assert data["defendant"]["bond_amount"] == 5000.0
    assert data["defendant"]["charges"][0]["charge"] == "GRAND THEFT >$750 <$5,000"

    # Indemnitor merged from chat + ID OCR
    assert data["indemnitor"]["full_name"] == "JOHN ROBERT SMITH"
    assert data["indemnitor"]["dl_number"] == "S530436783210"
    assert data["indemnitor"]["phone"] == "2395550199"
    assert data["indemnitor"]["email"] == "john@example.com"
    assert data["id_ocr_extracted"] is True

    # Never creates a DocuSeal submission directly
    assert len(cols["paperwork_packets"].docs) == 1
    pkt = cols["paperwork_packets"].docs[0]
    assert pkt["status"] == "staged"
    assert pkt["docuseal_submission_id"] is None
    assert pkt["surety_id"] == "palmetto"
    assert len(cols["intake_queue"].docs) == 1
    assert len(cols["arrests"].docs) == 1


def test_sl_hydrate_deep_link_opens_paperwork_packet():
    js = (ROOT / "dashboard" / "sl-hydrate.js").read_text(encoding="utf-8")
    assert "packet_id" in js
    assert "SLPaperwork.startFromBond" in js
