"""Staff missed-check-in evidence pack (BailSafe P0 A2)."""
from __future__ import annotations

import base64
import io
import json
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.auth.recovery_scope import path_allowed_for_recovery
from dashboard.services import checkin_evidence_service as svc

TEST_PIN = "918273"  # synthetic
JPEG = b"\xff\xd8" + (b"\x11" * 90) + b"\xff\xd9"
JPEG_B64 = base64.b64encode(JPEG).decode("ascii")


def _pdf_text(payload: bytes) -> str:
    """Read generated pack text with pdfplumber, which CI already installs."""
    import pdfplumber

    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def test_recovery_allowlist_excludes_evidence_pack():
    assert path_allowed_for_recovery("/api/checkin/evidence/BK-1", "GET") is False
    assert path_allowed_for_recovery("/api/checkin/evidence/BK-1/download", "GET") is False
    assert path_allowed_for_recovery("/api/checkin/enrollment-sla", "GET") is False


def test_pack_contains_fixture_log_selfie_gps_and_timestamps(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "UPLOAD_DIR", tmp_path)
    folder = tmp_path / "BK-1"
    folder.mkdir()
    stored = folder / "selfie_ab12.jpg"
    stored.write_bytes(JPEG)

    manifest = svc.build_evidence_manifest(
        booking_number="BK-1",
        bond={
            "booking_number": "BK-1",
            "defendant_name": "Fixture Defendant",
            "next_check_in_due": "2026-09-08T15:04:05+00:00",
            "last_check_in": "2026-09-01T15:04:05+00:00",
            "missed_check_ins": 1,
            "check_in_required": True,
            "check_in_frequency_days": 7,
        },
        check_in_logs=[
            {
                "checkin_id": "ci-old",
                "booking_number": "BK-1",
                "timestamp": "2026-08-01T00:00:00+00:00",
                "lat": 1.0,
                "lng": 2.0,
                "status": "verified",
            },
            {
                "checkin_id": "ci-1",
                "booking_number": "BK-1",
                "timestamp": "2026-09-01T15:04:05+00:00",
                "lat": 26.6406,
                "lng": -81.8723,
                "gps_accuracy_meters": 8.5,
                "has_selfie": True,
                "selfie_ref": "uploads/BK-1/selfie_ab12.jpg",
                "selfie_path": str(stored),
                "selfie_b64": JPEG_B64,
                "status": "verified",
            },
        ],
        bond_checkins=[
            {
                "booking_number": "BK-1",
                "checkin_at": "2026-09-03T12:00:00+00:00",
                "gps_lat": 26.5,
                "gps_lon": -81.8,
                "has_selfie": True,
                "selfie_thumbnail": "data:image/jpeg;base64,AAAA",
                "method": "portal_self_service",
                "status": "completed",
            }
        ],
        limit=1,
        generated_at="2026-10-07T12:00:00+00:00",
    )
    assert manifest["empty"] is False
    assert len(manifest["entries"]) == 2
    newest = manifest["entries"][0]
    assert newest["source"] == "bond_checkins"
    assert newest["lat"] == 26.5
    assert newest["lng"] == -81.8
    assert newest["selfie_ref"] == "bond_checkins:thumbnail_stub"
    assert newest["selfie_file"] is None

    log_row = manifest["entries"][1]
    assert log_row["timestamp"] == "2026-09-01T15:04:05+00:00"
    assert log_row["lat"] == 26.6406
    assert log_row["lng"] == -81.8723
    assert log_row["accuracy"] == 8.5
    assert log_row["selfie_ref"] == "uploads/BK-1/selfie_ab12.jpg"
    assert log_row["selfie_file"] == "selfies/ci-1.jpg"
    assert manifest["schedule"]["next_due"] == "2026-09-08T15:04:05+00:00"
    assert manifest["schedule"]["missed_check_ins"] == 1
    assert "1.0" not in json.dumps(manifest["entries"])

    blob = svc.render_evidence_zip(manifest)
    archive = zipfile.ZipFile(io.BytesIO(blob))
    packed = json.loads(archive.read("manifest.json"))
    assert packed["entries"][1]["lat"] == 26.6406
    assert archive.read("selfies/ci-1.jpg")[:2] == b"\xff\xd8"
    pdf_name = next(name for name in archive.namelist() if name.endswith(".pdf"))
    text = _pdf_text(archive.read(pdf_name))
    assert "26.64060" in text
    assert "-81.87230" in text
    assert "2026-09-01T15:04:05" in text
    assert "uploads/BK-1/selfie_ab12.jpg" in text
    assert "2026-09-08T15:04:05" in text


def test_outside_selfie_path_is_a_ref_only(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "UPLOAD_DIR", tmp_path)
    row = svc.normalize_checkin_row(
        {
            "checkin_id": "ci-path",
            "timestamp": "2026-09-02T12:00:00+00:00",
            "lat": 26.1,
            "lng": -81.8,
            "selfie_ref": "uploads/BK-1/missing.jpg",
            "selfie_path": "/etc/passwd",
            "has_selfie": True,
        },
        source="check_in_log",
    )
    assert row["selfie_ref"] == "uploads/BK-1/missing.jpg"
    assert row["_selfie_bytes"] is None
    assert row["selfie_file"] is None
    assert row["lat"] == 26.1


def test_empty_pack_does_not_invent_logs():
    manifest = svc.build_evidence_manifest(
        booking_number="BK-EMPTY",
        bond={
            "booking_number": "BK-EMPTY",
            "defendant_name": "No Logs",
            "next_check_in_due": "2026-10-01T00:00:00+00:00",
            "missed_check_ins": 2,
            "check_in_required": True,
        },
        check_in_logs=[],
        bond_checkins=[],
        generated_at="2026-10-07T12:00:00+00:00",
    )
    assert manifest["empty"] is True
    assert manifest["empty_reason"] == "no_check_in_logs"
    assert manifest["entries"] == []
    assert manifest["schedule"]["next_due"] == "2026-10-01T00:00:00+00:00"
    assert manifest["schedule"]["missed_check_ins"] == 2
    assert manifest["_files"] == []
    text = _pdf_text(svc.render_evidence_pdf(manifest))
    assert "No check-in logs on file" in text
    assert "2026-10-01T00:00:00" in text
    assert "26.640" not in text


class _Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def limit(self, n):
        return _Cursor(self.docs[:n])

    async def to_list(self, length=0):
        return self.docs[:length] if length else list(self.docs)


def _match(doc: dict, query: dict) -> bool:
    for key, expected in (query or {}).items():
        if key == "$or":
            if not any(_match(doc, clause) for clause in expected):
                return False
            continue
        actual = doc.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
            continue
        if actual != expected:
            return False
    return True


class _Col:
    def __init__(self, docs):
        self.docs = docs

    def find(self, query=None, projection=None):
        return _Cursor([doc for doc in self.docs if _match(doc, query or {})])

    async def find_one(self, query, projection=None):
        rows = [doc for doc in self.docs if _match(doc, query or {})]
        return rows[0] if rows else None

    async def insert_one(self, doc):
        self.docs.append(doc)
        return doc


@pytest.mark.asyncio
async def test_enrollment_sla_is_signed_and_unsent(monkeypatch):
    cols = {
        "paperwork_packets": _Col([
            {
                "booking_number": "BK-SIGN",
                "status": "signed",
                "signed_at": "2026-09-01T00:00:00+00:00",
                "defendant_name": "Signed Person",
            },
            {
                "booking_number": "BK-SENT",
                "status": "signed",
                "defendant_name": "Already Sent",
            },
            {
                "booking_number": "BK-DRAFT",
                "status": "draft",
                "defendant_name": "Draft Only",
            },
        ]),
        "bond_cases": _Col([]),
        "tasks": _Col([
            {
                "booking_number": "BK-SIGN",
                "task_type": "checkin_enroll",
                "status": "pending",
                "due_date": "2026-09-02T00:00:00+00:00",
            },
            {
                "booking_number": "BK-OPEN",
                "task_type": "checkin_enroll",
                "status": "pending",
            },
        ]),
        "active_bonds": _Col([
            {
                "booking_number": "BK-SIGN",
                "defendant_name": "Signed Person",
                "county": "Lee",
                "status": "active",
                "check_in_required": True,
            },
            {
                "booking_number": "BK-SENT",
                "defendant_name": "Already Sent",
                "checkin_link_last_sent_at": "2026-09-03T00:00:00+00:00",
            },
        ]),
    }
    monkeypatch.setattr(svc, "get_collection", lambda name: cols.get(name, _Col([])))
    data = await svc.list_signed_bonds_checkin_not_sent()
    assert data["label"] == "signed bond, check-in not sent"
    assert [item["booking_number"] for item in data["items"]] == ["BK-SIGN"]
    row = data["items"][0]
    assert row["checkin_link_last_sent_at"] is None
    assert "signed_packet" in row["reasons"]
    assert "pending_enroll_task" in row["reasons"]
    assert "phone" not in row


@pytest.mark.asyncio
async def test_unknown_booking_is_not_an_empty_pack(monkeypatch):
    empty = _Col([])

    async def _none(*_args, **_kwargs):
        return None

    empty.find_one = _none
    monkeypatch.setattr(svc, "get_collection", lambda name: empty)
    assert await svc.load_evidence_manifest("MISSING") is None


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(
        __import__(
            "dashboard.routers.checkin_evidence_api",
            fromlist=["checkin_evidence_bp"],
        ).checkin_evidence_bp
    )
    return TestClient(app)


def _auth_env():
    return patch.dict("os.environ", {"DASHBOARD_PIN": TEST_PIN, "SECRET_KEY": "ci-not-a-real-secret"})


def test_anonymous_and_non_staff_cannot_download(client):
    with _auth_env():
        anon = client.get("/api/checkin/evidence/BK-1/download")
        assert anon.status_code == 401
        from dashboard.auth.pin_middleware import COOKIE_NAME, _sign_token

        recovery = _sign_token(email="recovery@example.com", role="recovery", recovery_id="REC-1")
        denied = client.get(
            "/api/checkin/evidence/BK-1/download",
            cookies={COOKIE_NAME: recovery},
        )
        assert denied.status_code == 403
        assert denied.json()["error"] == "forbidden"

        sub = _sign_token(email="agent@example.com", role="sub_agent", agent_name="Office")
        sub_res = client.get(
            "/api/checkin/enrollment-sla",
            cookies={COOKIE_NAME: sub},
        )
        assert sub_res.status_code == 403


def test_staff_download_and_missing_booking(client, monkeypatch):
    manifest = svc.build_evidence_manifest(
        booking_number="BK-1",
        bond={"booking_number": "BK-1", "defendant_name": "Fixture Defendant"},
        check_in_logs=[{
            "checkin_id": "ci-1",
            "booking_number": "BK-1",
            "timestamp": "2026-09-01T15:04:05+00:00",
            "lat": 26.6406,
            "lng": -81.8723,
            "selfie_b64": JPEG_B64,
            "selfie_ref": "uploads/BK-1/selfie_ab12.jpg",
            "has_selfie": True,
            "status": "verified",
        }],
        generated_at="2026-10-07T12:00:00+00:00",
    )

    async def _load(booking_number, limit=20):
        if booking_number == "MISSING":
            return None
        return manifest

    monkeypatch.setattr(
        "dashboard.routers.checkin_evidence_api.load_evidence_manifest",
        _load,
    )
    monkeypatch.setattr(
        "dashboard.routers.checkin_evidence_api.audit_evidence_download",
        AsyncMock(),
    )
    with _auth_env():
        from dashboard.auth.pin_middleware import COOKIE_NAME, _sign_token

        token = _sign_token(email="staff@shamrockbailbonds.biz", role="staff")
        res = client.get(
            "/api/checkin/evidence/BK-1/download?format=zip",
            cookies={COOKIE_NAME: token},
        )
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("application/zip")
        archive = zipfile.ZipFile(io.BytesIO(res.content))
        packed = json.loads(archive.read("manifest.json"))
        assert packed["entries"][0]["lat"] == 26.6406
        assert packed["entries"][0]["selfie_ref"] == "uploads/BK-1/selfie_ab12.jpg"
        missing = client.get(
            "/api/checkin/evidence/MISSING",
            headers={"X-Admin-Token": TEST_PIN},
        )
        assert missing.status_code == 404
        assert missing.json()["error"] == "booking_not_found"
