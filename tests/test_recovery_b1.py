"""BailSafe B1 — recovery role is fail-closed and case shares are allowlisted."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient

from dashboard.auth.agent_scope import path_blocked_for_sub_agent
from dashboard.auth.pin_middleware import (
    COOKIE_NAME,
    PinAuthMiddleware,
    _sign_token,
    mount_login_routes,
)
from dashboard.auth.recovery_scope import path_allowed_for_recovery
from dashboard.routers.recovery import recovery_bp
from dashboard.routers.sub_agents import sub_agents_bp
from dashboard.services import recovery_case_service as recovery_svc
from tests._inmem_mongo import FakeDB

PIN = "test-pin-not-real"
SECRET_PHONE = "2395550199"
DEFENDANT_PHONE = "2395550177"
SECRET_EMAIL = "indemnitor-secret@example.com"
DEFENDANT_EMAIL = "defendant-secret@example.com"
BOND_AMOUNT = "25000.5"
PREMIUM = "2500.55"
POA = "OSI-SECRET-POA-9"
DOCUSEAL = "DS-SUB-SECRET"
PLAN = "PLAN-SECRET"
SSN = "999-88-7777"
INDEMNITOR_ADDRESS = "99 Secret Indemnitor Lane"
STAFF_NOTE = "Gate code is on the file jacket"


def _cookies(role: str, **kwargs) -> dict[str, str]:
    token = _sign_token(
        email=kwargs.get("email") or f"{role}@example.com",
        role=role,
        agent_name=kwargs.get("agent_name") or "",
        license_number=kwargs.get("license_number") or "",
        recovery_id=kwargs.get("recovery_id"),
        is_admin=role in ("god_admin", "admin"),
    )
    return {COOKIE_NAME: token}


@pytest.fixture
def db(monkeypatch, tmp_path):
    store = FakeDB()
    monkeypatch.setenv("DASHBOARD_PIN", PIN)
    monkeypatch.setenv("SECRET_KEY", "recovery-b1-test-secret")
    monkeypatch.setenv("RECOVERY_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr("dashboard.auth.pin_middleware.VALID_PINS", frozenset({PIN}))
    monkeypatch.setattr("dashboard.auth.pin_middleware.DASHBOARD_PIN", PIN)
    monkeypatch.setattr(recovery_svc, "get_collection", lambda name: store[name])
    return store


@pytest.fixture
def client(db):
    app = FastAPI()
    app.add_middleware(PinAuthMiddleware)
    mount_login_routes(app)
    app.include_router(recovery_bp)
    app.include_router(sub_agents_bp)

    def hit(name: str):
        def _handler():
            return {"hit": name}
        return _handler

    for method, path, name in (
        ("post", "/api/write-bond", "write-bond"),
        ("post", "/api/poa/void", "poa-void"),
        ("post", "/api/poa/execute", "poa-execute"),
        ("post", "/api/poa/reassign", "poa-reassign"),
        ("post", "/api/paperwork/packet/finalize", "docuseal-finalize"),
        ("post", "/api/paperwork/{packet_id}/docuseal", "docuseal-issue"),
        ("post", "/api/paperwork/post-release/remedy-doc", "recovery-warrant"),
        ("get", "/api/accounting/export/csv", "accounting-csv"),
        ("get", "/api/accounting/export/quickbooks", "accounting-qbo"),
        ("get", "/api/reports/bond-report.xlsx", "surety-export"),
        ("get", "/api/reports/forfeitures", "forfeiture-report"),
        ("get", "/api/payment-plans", "payment-plans"),
        ("post", "/api/finalize-bond/step2/{booking_number}", "finalize-bond"),
    ):
        getattr(app, method)(path)(hit(name))

    @app.get("/")
    def staff_home():
        return HTMLResponse("STAFF CRM")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return TestClient(app)


def _seed_forfeiture(db: FakeDB) -> None:
    when = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    db["active_bonds"].docs.append({
        "booking_number": "BK-44110",
        "Bond_Case_ID": "case-uuid-44110",
        "bond_case_id": "case-uuid-44110",
        "defendant_name": "CASEY FORFEIT",
        "defendant_dob": "",
        "county": "Lee",
        "state": "FL",
        "status": "forfeited",
        "case_number": "26CF009911",
        "court_case_number": "26CF009911",
        "forfeiture_order_date": when,
        "court_date": "2026-11-02",
        "court_location": "Lee County Justice Center",
        "defendant_address": "14 Palm Court, Fort Myers, FL 33901",
        "defendant_phone": DEFENDANT_PHONE,
        "defendant_email": DEFENDANT_EMAIL,
        "indemnitor_name": "Indy Secret",
        "indemnitor_phone": SECRET_PHONE,
        "indemnitor_email": SECRET_EMAIL,
        "indemnitor_address": INDEMNITOR_ADDRESS,
        "bond_amount": 25000.5,
        "premium_amount": 2500.55,
        "premium": 2500.55,
        "poa_number": POA,
        "docuseal_submission_id": DOCUSEAL,
        "payment_plan_id": PLAN,
        "ssn": SSN,
        "notes": f"call indemnitor at {SECRET_PHONE}",
        "surety_id": "osi",
    })
    db["arrests"].docs.append({
        "booking_number": "BK-44110",
        "county": "Lee",
        "state": "FL",
        "full_name": "CASEY FORFEIT",
        "date_of_birth": "1984-02-03",
        "address": "14 Palm Court",
        "city": "Fort Myers",
        "zip_code": "33901",
        "phone": DEFENDANT_PHONE,
        "email": DEFENDANT_EMAIL,
        "case_number": "26CF009911",
    })
    db["active_bonds"].docs.append({
        "booking_number": "BK-ACTIVE",
        "defendant_name": "STILL ACTIVE",
        "status": "active",
        "indemnitor_phone": SECRET_PHONE,
        "bond_amount": 25000.5,
    })


def _assert_allowlisted(payload: dict) -> None:
    blob = json.dumps(payload)
    for secret in (
        SECRET_PHONE,
        DEFENDANT_PHONE,
        SECRET_EMAIL,
        DEFENDANT_EMAIL,
        BOND_AMOUNT,
        PREMIUM,
        POA,
        DOCUSEAL,
        PLAN,
        SSN,
        INDEMNITOR_ADDRESS,
        "Indy Secret",
        "premium",
        "bond_amount",
        "poa_number",
        "docuseal",
        "indemnitor_phone",
    ):
        assert secret not in blob
    assert "CASEY FORFEIT" in blob
    assert "1984-02-03" in blob
    assert "BK-44110" in blob
    assert "26CF009911" in blob
    assert "14 Palm Court" in blob
    assert STAFF_NOTE in blob
    case = payload["case"] if "case" in payload else payload["cases"][0]
    assert set(case.keys()).issubset(recovery_svc.RECOVERY_CARD_KEYS)
    assert case["remittitur"]["clock_known"] is True
    assert case["remittitur"]["days_elapsed"] == 10
    assert "potential_remittitur_amount" not in case["remittitur"]
    assert "bond_amount" not in case["remittitur"]


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/write-bond"),
        ("POST", "/api/poa/void"),
        ("POST", "/api/poa/execute"),
        ("POST", "/api/poa/reassign"),
        ("POST", "/api/paperwork/packet/finalize"),
        ("POST", "/api/paperwork/PKT/docuseal"),
        ("POST", "/api/paperwork/post-release/remedy-doc"),
        ("GET", "/api/accounting/export/csv"),
        ("GET", "/api/accounting/export/quickbooks"),
        ("GET", "/api/reports/bond-report.xlsx"),
        ("GET", "/api/reports/forfeitures"),
        ("GET", "/api/payment-plans"),
        ("GET", "/api/active-bonds"),
        ("POST", "/api/recovery/shares"),
        ("POST", "/api/recovery/agents"),
        ("GET", "/api/recovery/forfeiture-candidates"),
        ("GET", "/"),
        ("GET", "/index.html"),
        ("GET", "/sl-paperwork.js"),
    ],
)
def test_recovery_allowlist_denies_staff_surfaces(method, path):
    assert path_allowed_for_recovery(path, method) is False


def test_recovery_allowlist_permits_case_desk_only():
    share = "11111111-1111-1111-1111-111111111111"
    doc = "22222222-2222-2222-2222-222222222222"
    assert path_allowed_for_recovery("/recovery", "GET") is True
    assert path_allowed_for_recovery("/api/session/me", "GET") is True
    assert path_allowed_for_recovery("/api/recovery/cases", "GET") is True
    assert path_allowed_for_recovery(f"/api/recovery/cases/{share}/notes", "POST") is True
    assert path_allowed_for_recovery(f"/api/recovery/cases/{share}/disposition", "POST") is True
    assert path_allowed_for_recovery(f"/api/recovery/cases/{share}/documents", "POST") is True
    assert path_allowed_for_recovery(f"/api/recovery/cases/{share}/documents/{doc}", "GET") is True
    assert path_allowed_for_recovery("/api/recovery/cases/not-a-uuid/notes", "POST") is False
    assert path_allowed_for_recovery("/api/recovery/cases/../../etc/passwd", "GET") is False
    assert path_blocked_for_sub_agent("/api/recovery/shares") is True


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/write-bond"),
        ("post", "/api/poa/void"),
        ("post", "/api/poa/execute"),
        ("post", "/api/poa/reassign"),
        ("post", "/api/paperwork/packet/finalize"),
        ("post", "/api/paperwork/PKT-1/docuseal"),
        ("post", "/api/paperwork/post-release/remedy-doc"),
        ("get", "/api/accounting/export/csv"),
        ("get", "/api/accounting/export/quickbooks"),
        ("get", "/api/reports/bond-report.xlsx"),
        ("get", "/api/reports/forfeitures"),
        ("get", "/api/payment-plans"),
        ("post", "/api/finalize-bond/step2/BK-44110"),
        ("post", "/api/recovery/shares"),
        ("get", "/api/recovery/forfeiture-candidates"),
        ("post", "/api/recovery/agents"),
    ],
)
def test_recovery_session_cannot_hit_staff_routes(client, method, path):
    response = getattr(client, method)(path, cookies=_cookies("recovery", recovery_id="REC-1001"))
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "recovery_route_denied"
    assert "hit" not in body


@pytest.mark.parametrize("role", ["god_admin", "admin", "staff"])
def test_staff_roles_still_reach_write_bond(client, role):
    response = client.post("/api/write-bond", cookies=_cookies(role))
    assert response.status_code == 200
    assert response.json()["hit"] == "write-bond"


def test_recovery_pages_redirect_and_portal_opens(client):
    home = client.get("/", cookies=_cookies("recovery", recovery_id="REC-1001"), follow_redirects=False)
    assert home.status_code == 302
    assert home.headers["location"] == "/recovery"
    assert "STAFF CRM" not in home.text

    portal = client.get("/recovery", cookies=_cookies("recovery", recovery_id="REC-1001"))
    assert portal.status_code == 200
    assert 'data-recovery-portal="b1"' in portal.text

    health = client.get("/health", cookies=_cookies("recovery", recovery_id="REC-1001"))
    assert health.status_code == 200

    asset = client.get("/sl-accounting.js", cookies=_cookies("recovery", recovery_id="REC-1001"))
    assert asset.status_code == 403


def test_share_projects_allowlist_and_audits(client, db):
    _seed_forfeiture(db)
    staff = _cookies("staff", email="office@shamrockbailbonds.biz")
    created = client.post(
        "/api/recovery/shares",
        cookies=staff,
        json={
            "booking_number": "BK-44110",
            "staff_notes": STAFF_NOTE,
            "expires_in_days": 30,
        },
    )
    assert created.status_code == 200, created.text
    _assert_allowlisted(created.json())
    share_id = created.json()["share_id"]

    candidates = client.get("/api/recovery/forfeiture-candidates", cookies=staff)
    assert candidates.status_code == 200
    blob = candidates.text
    assert SECRET_PHONE not in blob
    assert BOND_AMOUNT not in blob
    assert "BK-ACTIVE" not in blob
    assert "BK-44110" in blob

    recovery = _cookies(
        "recovery",
        email="recovery-rec-1001@agents.shamrockbailbonds.biz",
        agent_name="Alex Rivera",
        recovery_id="REC-1001",
    )
    listed = client.get("/api/recovery/cases", cookies=recovery)
    assert listed.status_code == 200
    _assert_allowlisted(listed.json())

    noted = client.post(
        f"/api/recovery/cases/{share_id}/notes",
        cookies=recovery,
        json={"text": "Family confirmed he left the county line."},
    )
    assert noted.status_code == 200
    assert any(note["text"].startswith("Family confirmed") for note in noted.json()["case"]["notes"])

    marked = client.post(
        f"/api/recovery/cases/{share_id}/disposition",
        cookies=recovery,
        json={"disposition": "located"},
    )
    assert marked.status_code == 200
    assert marked.json()["case"]["disposition"] == "located"

    staff_view = client.get("/api/recovery/cases", cookies=staff)
    assert staff_view.status_code == 200
    card = staff_view.json()["cases"][0]
    assert card["disposition"] == "located"
    assert any("Family confirmed" in note["text"] for note in card["notes"])
    _assert_allowlisted({"case": card, "cases": staff_view.json()["cases"]})

    actions = [row.get("action") for row in db["audit_events"].docs]
    assert "recovery_case_shared" in actions
    assert "recovery_note_added" in actions
    assert "recovery_disposition_set" in actions
    audit_blob = json.dumps(db["audit_events"].docs, default=str)
    assert SECRET_PHONE not in audit_blob
    assert STAFF_NOTE not in audit_blob


def test_unshare_and_expiry_remove_recovery_access(client, db):
    _seed_forfeiture(db)
    staff = _cookies("god_admin")
    recovery = _cookies("recovery", recovery_id="REC-9")
    created = client.post(
        "/api/recovery/shares",
        cookies=staff,
        json={"booking_number": "BK-44110", "staff_notes": STAFF_NOTE},
    )
    share_id = created.json()["share_id"]
    assert client.get(f"/api/recovery/cases/{share_id}", cookies=recovery).status_code == 200

    revoked = client.post(f"/api/recovery/shares/{share_id}/revoke", cookies=staff)
    assert revoked.status_code == 200
    hidden = client.get(f"/api/recovery/cases/{share_id}", cookies=recovery)
    assert hidden.status_code == 404
    assert client.get("/api/recovery/cases", cookies=recovery).json()["count"] == 0
    assert "recovery_case_unshared" in [row.get("action") for row in db["audit_events"].docs]

    created_again = client.post(
        "/api/recovery/shares",
        cookies=staff,
        json={"booking_number": "BK-44110", "staff_notes": STAFF_NOTE},
    )
    second_id = created_again.json()["share_id"]
    for doc in db["recovery_case_shares"].docs:
        if doc.get("share_id") == second_id:
            doc["expires_at"] = "2000-01-01T00:00:00+00:00"
    expired = client.get("/api/recovery/cases", cookies=recovery)
    assert expired.json()["count"] == 0
    missing = client.get(f"/api/recovery/cases/{second_id}", cookies=recovery)
    assert missing.status_code == 404
    assert "recovery_case_expired" in [row.get("action") for row in db["audit_events"].docs]


def test_scoped_share_hides_other_recovery_agents(client, db):
    _seed_forfeiture(db)
    db["recovery_agents"].docs.append({
        "recovery_id": "REC-1",
        "display_name": "One",
        "is_active": True,
    })
    db["recovery_agents"].docs.append({
        "recovery_id": "REC-2",
        "display_name": "Two",
        "is_active": True,
    })
    staff = _cookies("admin")
    created = client.post(
        "/api/recovery/shares",
        cookies=staff,
        json={"booking_number": "BK-44110", "recovery_agent_id": "REC-1", "staff_notes": STAFF_NOTE},
    )
    assert created.status_code == 200
    share_id = created.json()["share_id"]
    owner = _cookies("recovery", recovery_id="REC-1", agent_name="One")
    other = _cookies("recovery", recovery_id="REC-2", agent_name="Two")
    assert client.get(f"/api/recovery/cases/{share_id}", cookies=owner).status_code == 200
    assert client.get(f"/api/recovery/cases/{share_id}", cookies=other).status_code == 404
    assert client.get("/api/recovery/cases", cookies=other).json()["count"] == 0


def test_sub_agent_and_active_bond_cannot_share(client, db):
    _seed_forfeiture(db)
    sub = client.post(
        "/api/recovery/shares",
        cookies=_cookies("sub_agent", license_number="P1", agent_name="Sub"),
        json={"booking_number": "BK-44110"},
    )
    assert sub.status_code == 403

    staff = _cookies("staff")
    refused = client.post(
        "/api/recovery/shares",
        cookies=staff,
        json={"booking_number": "BK-ACTIVE", "staff_notes": "nope"},
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "not_forfeiture"

    mismatch = client.post(
        "/api/recovery/shares",
        cookies=staff,
        json={"booking_number": "BK-44110", "bond_case_id": "other-case"},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["code"] == "identity_conflict"


def test_recovery_login_and_document_roundtrip(client, db):
    _seed_forfeiture(db)
    staff = _cookies("staff")
    provision = client.post(
        "/api/recovery/agents",
        cookies=staff,
        json={"recovery_id": "rec-44", "display_name": "Riley Quinn"},
    )
    assert provision.status_code == 200
    assert provision.json()["recovery_id"] == "REC-44"

    bad = client.post("/login", json={"pin": PIN, "recovery_id": "REC-NOPE"})
    assert bad.status_code == 403

    logged_in = client.post("/login", json={"pin": PIN, "recovery_id": "REC-44"})
    assert logged_in.status_code == 200
    assert logged_in.json()["role"] == "recovery"
    assert "sl_session" in logged_in.cookies

    created = client.post(
        "/api/recovery/shares",
        cookies=staff,
        json={"booking_number": "BK-44110", "staff_notes": STAFF_NOTE, "recovery_agent_id": "REC-44"},
    )
    share_id = created.json()["share_id"]
    me = client.get("/api/session/me")
    assert me.status_code == 200
    assert me.json()["is_recovery"] is True
    assert me.json()["recovery_id"] == "REC-44"

    upload = client.post(
        f"/api/recovery/cases/{share_id}/documents",
        files={"file": ("note.pdf", b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF", "application/pdf")},
    )
    assert upload.status_code == 200
    doc_id = upload.json()["case"]["documents"][0]["doc_id"]
    downloaded = client.get(f"/api/recovery/cases/{share_id}/documents/{doc_id}")
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"%PDF")

    rejected = client.post(
        f"/api/recovery/cases/{share_id}/documents",
        files={"file": ("x.pdf", b"<html>nope</html>", "application/pdf")},
    )
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "document_type_denied"
    assert "recovery_document_uploaded" in [row.get("action") for row in db["audit_events"].docs]
    assert "recovery_agent_provisioned" in [row.get("action") for row in db["audit_events"].docs]


def test_unknown_recovery_login_does_not_echo_contact_data(client):
    response = client.post("/login", json={"pin": "wrong", "recovery_id": "REC-1"})
    assert response.status_code == 401
    assert SECRET_PHONE not in response.text
