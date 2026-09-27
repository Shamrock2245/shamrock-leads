"""Kiosk: defendant allowed, scan→confirm, co-indemnitor never overwrites the
primary indemnitor, 3-minute idle reset, /done auto-reset, no localStorage PII,
public scan uses the PIN-scoped endpoint (2026-09-27)."""
from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from dashboard.main import app
from tests._inmem_mongo import FakeDB

client = TestClient(app)

PACKET = {
    "packet_id": "PKT-KIOSK1",
    "status": "pending_signature",
    "defendant_name": "Booking Name",
    "indemnitor_id": "IND-1",
    "client_fields": {"indemnitor_name": "Primary Person", "indemnitor_address": "1 Primary St"},
    "docuseal_submitters": [
        {"role": "indemnitor", "id": 11, "slug": "i"},
        {"role": "coindemnitor", "id": 22, "slug": "c"},
        {"role": "defendant", "id": 33, "slug": "d"},
    ],
}
EXTRACTED = {"full_name": "CO SIGNER", "first_name": "CO", "last_name": "SIGNER",
             "address": "9 Other Rd", "city": "Naples", "state": "FL", "zip": "34102",
             "dob": "1970-01-01", "dl_number": "S999"}


def _db_with_packet(packet=PACKET):
    db = FakeDB()
    db["paperwork_packets"].docs.append(dict(packet))
    db["indemnitors"].docs.append({"indemnitor_id": "IND-1", "name": "Primary Person"})
    return db


def _scan(db, role):
    scanner = AsyncMock(return_value={"success": True, "extracted": EXTRACTED})
    with patch("dashboard.routers.pin_portal.get_collection", side_effect=db.get_collection), \
         patch("dashboard.services.id_scanner_service.IDScannerService.scan_id_image", scanner):
        r = client.post("/api/portal/kiosk-id-ocr",
                        data={"packet_id": "PKT-KIOSK1", "role": role},
                        files={"file": ("id.jpg", b"fake-bytes", "image/jpeg")})
    return r


def test_scan_writes_nothing_and_returns_token():
    db = _db_with_packet()
    r = _scan(db, "coindemnitor")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["confirm_required"] is True and d["scan_token"]
    assert d["fields"]["coindemnitor_name"] == "Co Signer" or d["fields"]["coindemnitor_name"]
    assert "indemnitor_name" not in d["fields"]
    assert db["paperwork_packets"].docs[0]["client_fields"]["indemnitor_name"] == "Primary Person"


def test_coindemnitor_confirm_never_touches_primary():
    db = _db_with_packet()
    token = _scan(db, "coindemnitor").json()["scan_token"]
    upd = AsyncMock()
    with patch("dashboard.routers.pin_portal.get_collection", side_effect=db.get_collection), \
         patch("dashboard.services.docuseal_service.DocuSealService.update_submitter", upd):
        r = client.post("/api/portal/kiosk-id-confirm", json={
            "scan_token": token,
            "fields": {"coindemnitor_phone": "2395550000", "coindemnitor_email": "co@example.com",
                       "indemnitor_name": "HIJACK"},  # other-role keys are ignored
        })
    assert r.status_code == 200, r.text
    assert r.json()["pushed_to_docuseal"] is True
    submitter_id = upd.await_args.args[0]
    assert submitter_id == 22  # the co-indemnitor submitter only
    pkt = db["paperwork_packets"].docs[0]
    assert pkt["client_fields"]["indemnitor_name"] == "Primary Person"
    assert pkt["client_fields"]["indemnitor_address"] == "1 Primary St"
    assert pkt["coindemnitor_fields"]["coindemnitor_phone"] == "2395550000"
    assert db["indemnitors"].docs[0]["name"] == "Primary Person"


def test_coindemnitor_without_signer_does_not_fall_back_to_indemnitor():
    packet = dict(PACKET, docuseal_submitters=[{"role": "indemnitor", "id": 11}])
    db = _db_with_packet(packet)
    token = _scan(db, "coindemnitor").json()["scan_token"]
    upd = AsyncMock()
    with patch("dashboard.routers.pin_portal.get_collection", side_effect=db.get_collection), \
         patch("dashboard.services.docuseal_service.DocuSealService.update_submitter", upd):
        r = client.post("/api/portal/kiosk-id-confirm", json={"scan_token": token, "fields": {}})
    assert r.json()["pushed_to_docuseal"] is False
    upd.assert_not_awaited()


def test_defendant_kiosk_scan_allowed_booking_name_wins():
    db = _db_with_packet()
    token = _scan(db, "defendant").json()["scan_token"]
    upd = AsyncMock()
    with patch("dashboard.routers.pin_portal.get_collection", side_effect=db.get_collection), \
         patch("dashboard.services.docuseal_service.DocuSealService.update_submitter", upd):
        r = client.post("/api/portal/kiosk-id-confirm", json={"scan_token": token, "fields": {"defendant_phone": "2395551111"}})
    assert r.status_code == 200
    assert upd.await_args.args[0] == 33
    pkt = db["paperwork_packets"].docs[0]
    assert pkt["client_fields"]["defendant_address"] == "9 Other Rd"
    assert "defendant_name" not in upd.await_args.kwargs["values"]
    assert pkt["defendant_name_scanned"]


def test_confirm_rejects_tampered_token():
    r = client.post("/api/portal/kiosk-id-confirm", json={"scan_token": "abc.def", "fields": {}})
    assert r.status_code == 400


def test_kiosk_sign_page_for_defendant_has_scan_and_kiosk_done_redirect():
    db = _db_with_packet()
    with patch("dashboard.routers.pin_portal.get_collection", side_effect=db.get_collection):
        r = client.get("/sign/PKT-KIOSK1/defendant?mode=kiosk")
    assert r.status_code == 200
    assert "Scan your ID" in r.text and "/done?kiosk=1" in r.text
    assert "localStorage.setItem" not in r.text


def test_done_kiosk_auto_resets_and_kiosk_page():
    r = client.get("/api/portal/done?kiosk=1")
    assert "location.replace('/kiosk')" in r.text and "sessionStorage.clear()" in r.text
    r2 = client.get("/kiosk")
    assert r2.status_code == 200 and "Ready for the next client" in r2.text
    assert r2.headers.get("cache-control") == "no-store"


def test_portal_ui_no_localstorage_pii_and_pin_scoped_scan():
    html = client.get("/api/portal/portal-ui").text
    assert "localStorage.setItem" not in html
    assert "/api/id/scan-ocr" not in html
    assert "/api/portal/id-ocr" in html
    assert "Still there?" not in html
    kiosk_html = client.get("/api/portal/portal-ui?mode=kiosk").text
    assert "Still there?" in kiosk_html and "180 * 1000" in kiosk_html


def test_kiosk_home_is_public_exact_path_only():
    """The idle reset lands on /kiosk; it must not bounce to the staff login
    (and a staff session must never be needed on the lobby tablet)."""
    from dashboard.auth import pin_middleware as pm

    assert "/kiosk" in pm.OPEN_PATHS
    assert not any(p.startswith("/kiosk") for p in pm.OPEN_PREFIXES)
