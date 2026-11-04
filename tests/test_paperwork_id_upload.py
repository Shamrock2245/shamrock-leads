"""Launch mode id-upload on the paperwork portal.

Synthetic TEST- records only. The signer email is the staff test address.
No DocuSeal send and no SwipeSimple charge.
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from dashboard.main import app
from tests._inmem_mongo import FakeDB

client = TestClient(app)

TOKEN = "PORTAL-TEST-IDUPLOAD"
PACKET_ID = "PKT-TEST-IDUP"
SIGN_URL = "https://sign.shamrockbailbonds.biz/s/TEST-SHOULD-NOT-LEAK"
PAY_URL = "https://swipesimple.com/links/TEST-SHOULD-NOT-LEAK"
EMAIL = "admin@shamrockbailbonds.biz"
FUTURE = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
PAPERWORK = {"host": "paperwork.shamrockbailbonds.biz"}

_BANNED = (
    "openDocuSealForm",
    "sign.shamrockbailbonds.biz",
    "docuseal",
    "swipesimple",
    "Send Access PIN",
    "Signing link",
    "docuseal-form",
    "Step 2",
)


def _packet():
    return {
        "packet_id": PACKET_ID,
        "booking_number": "TEST-IDUP-1",
        "poa_number": "TEST-POA-1",
        "status": "pending_signature",
        "voided": False,
        "indemnitor_email": EMAIL,
        "sign_url": SIGN_URL,
        "signing_link": SIGN_URL,
        "id_scan": {"scan_id": "TEST-SCAN-DEF", "role": "defendant"},
        "id_ocr": {"scan_id": "TEST-SCAN-IND", "role": "indemnitor"},
        "id_extracted": {"full_name": "TEST Kept Extract", "dl_state": "FL"},
        "id_scan_history": [{"id_scan": {"scan_id": "TEST-SCAN-HIST"}}],
        "id_scan_records": [{"scan_id": "TEST-SCAN-EXISTING", "role": "coindemnitor"}],
        "co_indemnitor": {"id_scan": {"scan_id": "TEST-SCAN-CO"}},
        "defendant": {"id_scan": {"scan_id": "TEST-SCAN-DEF-NESTED"}},
    }


def _session(**extra):
    doc = {
        "session_token": TOKEN,
        "verified": True,
        "expires_at": FUTURE,
        "role": "indemnitor",
        "packet_id": PACKET_ID,
        "id_extracted": {"full_name": "TEST First Scan", "dl_state": "GA"},
        "id_scan": {"scan_id": "TEST-SCAN-SESSION"},
    }
    doc.update(extra)
    return doc


def _world(monkeypatch, session=None, packet=None):
    db = FakeDB()
    db["portal_pins"].docs.append(copy.deepcopy(session if session is not None else _session()))
    if packet is not False:
        db["paperwork_packets"].docs.append(copy.deepcopy(packet if packet is not None else _packet()))
    db["indemnitors"].docs.append({
        "indemnitor_id": "TEST-IND-1",
        "email": EMAIL,
        "id_scan": {"scan_id": "TEST-SCAN-INDEMNITOR"},
        "id_scan_history": [{"id_scan": {"scan_id": "TEST-SCAN-IND-HIST"}}],
    })
    db["audit_events"].docs.append({
        "event_id": "TEST-AUDIT-KEEP",
        "event_type": "earlier_review",
        "reason": "kept",
    })
    monkeypatch.setattr("dashboard.routers.pin_portal.get_collection", db.get_collection)
    return db


def _scanner(monkeypatch, calls):
    async def _scan(image_bytes, filename=""):
        calls["n"] += 1
        calls["bytes"].append(bytes(image_bytes))
        return {
            "success": True,
            "extracted": {
                "full_name": "TEST Signer",
                "dl_state": "FL",
                "dl_number": "D1234567",
                "address": "1 Test St",
                "dob": "1990-01-01",
            },
            "portrait_jpeg_b64": "PORTRAIT-SHOULD-NOT-STORE",
        }

    monkeypatch.setattr(
        "dashboard.services.id_scanner_service.IDScannerService.scan_id_image",
        _scan,
    )


def _block_remote(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("remote paperwork call")

    monkeypatch.setattr(
        "dashboard.services.docuseal_service.DocuSealService.update_submitter",
        _boom,
    )
    monkeypatch.setattr(
        "dashboard.services.docuseal_service.DocuSealService.create_submission",
        _boom,
    )
    monkeypatch.setattr(
        "dashboard.services.docuseal_service.DocuSealService.create_submission_for_packet",
        _boom,
    )
    monkeypatch.setattr("dashboard.services.payment_links.payment_link_for", _boom)


def _upload(token, body, *, query=True):
    files = {"id_image": ("id.jpg", body, "image/jpeg")}
    data = {"full_name": "Not From The Server", "dl_number": "D000"}
    if query:
        return client.post(f"/api/portal/id-upload?st={token}", files=files, data=data)
    return client.post("/api/portal/id-upload", files=files, data={**data, "session_token": token})


def _page(path):
    return client.get(path, headers=PAPERWORK)


def test_id_upload_mode_shows_only_the_id_step():
    html = _page("/api/portal/portal-ui?mode=id-upload").text
    root = _page("/?mode=id-upload&link=" + SIGN_URL + "&st=" + TOKEN).text
    sign = client.get(
        f"/sign/{PACKET_ID}/indemnitor?mode=id-upload&link={SIGN_URL}",
        headers=PAPERWORK,
    ).text
    for body in (html, root, sign):
        assert 'data-launch-mode="id-upload"' in body
        assert 'name="id_image"' in body
        assert "Upload your ID" in body
        assert "/api/portal/id-upload" in body
        lowered = body.lower()
        for banned in _BANNED:
            assert banned.lower() not in lowered
        assert "TEST-SHOULD-NOT-LEAK" not in body
        assert "Still there?" not in body


def test_second_upload_does_not_overwrite_the_first_scan(monkeypatch, caplog):
    db = _world(monkeypatch)
    calls = {"n": 0, "bytes": []}
    _scanner(monkeypatch, calls)
    _block_remote(monkeypatch)
    packet_before = copy.deepcopy(db["paperwork_packets"].docs[0])
    indemnitor_before = copy.deepcopy(db["indemnitors"].docs[0])
    session_before = copy.deepcopy(db["portal_pins"].docs[0])
    audit_before = copy.deepcopy(db["audit_events"].docs[0])

    caplog.set_level(logging.INFO)
    first = _upload(TOKEN, b"first-id-photo")
    assert first.status_code == 200, first.text
    first_id = first.json()["scan_id"]
    second = _upload(TOKEN, b"second-id-photo")
    assert second.status_code == 200, second.text
    second_id = second.json()["scan_id"]
    assert first_id != second_id
    assert calls["n"] == 2
    assert calls["bytes"] == [b"first-id-photo", b"second-id-photo"]

    session = db["portal_pins"].docs[0]
    packet = db["paperwork_packets"].docs[0]
    assert [row["scan_id"] for row in session["id_scan_records"]] == [first_id, second_id]
    assert [row["scan_id"] for row in packet["id_scan_records"]] == [
        "TEST-SCAN-EXISTING", first_id, second_id,
    ]
    assert session["id_extracted"] == session_before["id_extracted"]
    assert session["id_scan"] == session_before["id_scan"]
    for key in ("id_scan", "id_ocr", "id_extracted", "id_scan_history", "co_indemnitor", "defendant", "poa_number", "sign_url"):
        assert packet[key] == packet_before[key]
    assert packet["id_scan_records"][0] == packet_before["id_scan_records"][0]
    assert db["indemnitors"].docs[0] == indemnitor_before
    assert db["audit_events"].docs[0] == audit_before
    assert len(db["audit_events"].docs) == 3
    assert [row["event_type"] for row in db["audit_events"].docs[1:]] == [
        "id_upload_saved", "id_upload_saved",
    ]
    stored = session["id_scan_records"][0]
    assert stored["extracted"] == {"full_name": "TEST Signer", "dl_state": "FL"}
    assert "dl_number" not in stored["extracted"]
    assert "portrait" not in str(stored)
    app_logs = "\n".join(
        rec.getMessage() for rec in caplog.records if rec.name.startswith("dashboard.")
    )
    assert EMAIL not in app_logs
    assert "D1234567" not in app_logs
    assert TOKEN not in app_logs
    assert "1 Test St" not in app_logs


@pytest.mark.parametrize(
    "session_extra,error,status",
    [
        ({"session_token": "PORTAL-TEST-OTHER"}, "token_invalid", 401),
        ({"verified": False}, "token_unverified", 401),
        ({"status": "unverified"}, "token_unverified", 401),
        ({"expires_at": "2020-01-01T00:00:00+00:00"}, "token_expired", 401),
        ({"voided": True}, "token_voided", 403),
        ({"revoked": True}, "token_voided", 403),
        ({"status": "voided"}, "token_voided", 403),
    ],
)
def test_invalid_token_is_rejected_before_ocr(monkeypatch, session_extra, error, status):
    session = _session()
    session.update(session_extra)
    db = _world(monkeypatch, session=session)
    calls = {"n": 0, "bytes": []}
    _scanner(monkeypatch, calls)
    packet_before = copy.deepcopy(db["paperwork_packets"].docs[0])
    audits_before = len(db["audit_events"].docs)

    response = _upload(TOKEN, b"should-not-scan")
    assert response.status_code == status
    assert response.json() == {"success": False, "error": error}
    assert calls["n"] == 0
    assert db["paperwork_packets"].docs[0] == packet_before
    assert "id_scan_records" not in db["portal_pins"].docs[0]
    assert len(db["audit_events"].docs) == audits_before


def test_voided_packet_is_rejected_before_ocr(monkeypatch):
    packet = _packet()
    packet["voided"] = True
    packet["status"] = "voided"
    db = _world(monkeypatch, packet=packet)
    calls = {"n": 0, "bytes": []}
    _scanner(monkeypatch, calls)
    before = copy.deepcopy(packet)

    response = _upload(TOKEN, b"should-not-scan")
    assert response.status_code == 403
    assert response.json()["error"] == "token_voided"
    assert calls["n"] == 0
    assert db["paperwork_packets"].docs[0] == before
    assert len(db["audit_events"].docs) == 1


def test_client_json_is_not_ocr_and_is_refused_without_a_token(monkeypatch):
    _world(monkeypatch)
    calls = {"n": 0, "bytes": []}
    _scanner(monkeypatch, calls)

    missing = client.post("/api/portal/id-upload", json={"full_name": "TEST Signer", "dl_state": "FL"})
    assert missing.status_code == 401
    assert missing.json()["error"] == "token_invalid"
    bad = client.post(
        "/api/portal/id-upload?st=not-a-token",
        json={"extracted": {"full_name": "TEST Signer", "dl_number": "D1234567"}},
    )
    assert bad.status_code == 401
    assert bad.json()["error"] == "token_invalid"
    valid = client.post(
        f"/api/portal/id-upload?st={TOKEN}",
        json={"full_name": "TEST Signer", "dl_state": "FL", "dl_number": "D1234567"},
    )
    assert valid.status_code == 400
    assert valid.json()["error"] == "scan_image_required"
    assert calls["n"] == 0


def test_id_upload_produces_no_docuseal_url_or_pay_link(monkeypatch):
    db = _world(monkeypatch)
    calls = {"n": 0, "bytes": []}
    _scanner(monkeypatch, calls)
    _block_remote(monkeypatch)

    response = _upload(TOKEN, b"id-photo")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"success", "saved", "inserted", "scan_id"}
    text = response.text.lower()
    assert "sign.shamrockbailbonds.biz" not in text
    assert "docuseal" not in text
    assert "swipesimple" not in text
    assert "test-should-not-leak" not in text
    assert PAY_URL.lower() not in text
    assert "signing_link" not in body
    assert "sign_url" not in body
    page = _page(f"/?mode=id-upload&link={SIGN_URL}&pay={PAY_URL}").text.lower()
    assert "sign.shamrockbailbonds.biz" not in page
    assert "swipesimple" not in page
    assert "docuseal" not in page
    assert db["paperwork_packets"].docs[0]["sign_url"] == SIGN_URL
    assert db["paperwork_packets"].docs[0]["poa_number"] == "TEST-POA-1"


def test_poa_change_does_not_invalidate_the_upload_link(monkeypatch):
    db = _world(monkeypatch)
    calls = {"n": 0, "bytes": []}
    _scanner(monkeypatch, calls)
    _block_remote(monkeypatch)

    first = _upload(TOKEN, b"before-poa-change")
    assert first.status_code == 200, first.text
    first_id = first.json()["scan_id"]
    db["paperwork_packets"].docs[0]["poa_number"] = "TEST-POA-2"
    second = _upload(TOKEN, b"after-poa-change")
    assert second.status_code == 200, second.text
    packet = db["paperwork_packets"].docs[0]
    assert packet["poa_number"] == "TEST-POA-2"
    assert [row["scan_id"] for row in packet["id_scan_records"]] == [
        "TEST-SCAN-EXISTING", first_id, second.json()["scan_id"],
    ]
    assert packet["id_scan"]["scan_id"] == "TEST-SCAN-DEF"
    assert packet["id_ocr"]["scan_id"] == "TEST-SCAN-IND"


def test_unknown_mode_falls_back_to_the_normal_flow():
    html = _page("/api/portal/portal-ui?mode=banana").text
    root = _page("/?mode=not-a-real-mode").text
    for body in (html, root):
        assert 'data-launch-mode="id-upload"' not in body
        assert "Send Access PIN" in body
        assert "Signing link" in body
        assert "openDocuSealForm" in body
        assert "Step 1: Scan ID" in body
        assert "mode === 'ipad' || mode === 'inperson' || mode === 'kiosk'" in body
    kiosk = _page("/api/portal/portal-ui?mode=kiosk").text
    assert "Still there?" in kiosk
    assert 'data-launch-mode="id-upload"' not in kiosk
    in_person = _page("/api/portal/portal-ui?mode=inperson").text
    assert "Still there?" in in_person
