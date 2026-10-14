"""Powers-pack downloads archive one generated_reports row.

Mocked Mongo and TEST- rows only. No live database.
"""

from __future__ import annotations

import base64
from unittest.mock import patch

import pytest
from bson import ObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.auth.pin_middleware import COOKIE_NAME, _get_serializer, _sign_token


_SESSION_EMAIL = "test-staff@example.invalid"
_SESSION_NAME = "TEST-Agent"
_OTHER_ACTOR = "TEST-NOT-THE-ACTOR"


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=-1):
        reverse = direction == -1
        self._docs = sorted(
            self._docs,
            key=lambda doc: (doc.get(key) is None, doc.get(key) or ""),
            reverse=reverse,
        )
        return self

    async def to_list(self, limit):
        return [dict(doc) for doc in self._docs[:limit]]


class _Col:
    def __init__(self, docs=None):
        self.docs = [dict(doc) for doc in (docs or [])]

    def find(self, query=None, projection=None):
        rows = []
        for doc in self.docs:
            row = dict(doc)
            if projection:
                for field, flag in projection.items():
                    if flag == 0:
                        row.pop(field, None)
            rows.append(row)
        return _Cursor(rows)

    async def find_one(self, query):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in (query or {}).items()):
                return dict(doc)
        return None

    async def insert_one(self, doc):
        stored = dict(doc)
        stored.setdefault("_id", ObjectId())
        self.docs.append(stored)
        return stored


class _DB(dict):
    def __getitem__(self, key):
        if key not in self:
            super().__setitem__(key, _Col())
        return super().__getitem__(key)


def _inventory():
    return [{
        "surety_id": "palmetto",
        "poa_number": "TEST-1001",
        "status": "assigned",
        "date_executed": "2026-09-20",
        "bond_amount": 8000,
        "defendant_name": "TEST-DEFENDANT",
        "booking_number": "TEST-BK-1",
    }]


def _db():
    return _DB({
        "poa_inventory": _Col(_inventory()),
        "active_bonds": _Col(),
        "audit_events": _Col(),
    })


@pytest.fixture
def reports_app():
    from dashboard.routers.reports import reports_bp

    app = FastAPI()
    app.include_router(reports_bp)
    return app


@pytest.fixture
def session_secret(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-powers-pack-archive-secret")


def _cookie(email=_SESSION_EMAIL, agent_name=_SESSION_NAME):
    token = _sign_token(email=email, role="staff", agent_name=agent_name, is_admin=False)
    return {COOKIE_NAME: token}


def _name_only_cookie(agent_name=_SESSION_NAME):
    token = _get_serializer().dumps({
        "auth": True,
        "t": 1,
        "role": "staff",
        "email": "",
        "agent_name": agent_name,
    })
    return {COOKIE_NAME: token}


def _params(**extra):
    params = {
        "surety": "Palmetto",
        "start_date": "2026-09-01",
        "end_date": "2026-09-30",
        "actor": _OTHER_ACTOR,
    }
    params.update(extra)
    return params


def _spoof_headers():
    return {
        "X-Actor": _OTHER_ACTOR,
        "X-Staff-Email": _OTHER_ACTOR,
    }


@patch("dashboard.routers.reports.get_db")
def test_building_a_pack_inserts_one_row(mock_get_db, reports_app, session_secret):
    db = _db()
    mock_get_db.return_value = db
    client = TestClient(reports_app)
    resp = client.get(
        "/api/reports/powers-pack/combined",
        params=_params(),
        headers=_spoof_headers(),
        cookies=_cookie(),
    )
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["x-history-complete"] == "true"
    assert resp.headers["x-transfer-partial"] == "false"
    assert resp.headers["x-execution-count"] == "1"
    assert resp.headers["x-void-count"] == "0"
    assert resp.headers["x-transfer-count"] == "0"
    assert 'filename="Palmetto_Combined_Powers_2026-09-30.xlsx"' in resp.headers["content-disposition"]
    assert resp.content[:2] == b"PK"

    stored = db["generated_reports"].docs
    assert len(stored) == 1
    row = stored[0]
    assert row["report_type"] == "powers_pack"
    assert row["source"] == "dashboard_xlsx"
    assert row["surety_id"] == "palmetto"
    assert row["pack"] == "combined"
    assert row["start_date"] == "2026-09-01"
    assert row["end_date"] == "2026-09-30"
    assert row["filename"] == "Palmetto_Combined_Powers_2026-09-30.xlsx"
    assert row["counts"] == {
        "execution": 1,
        "void": 0,
        "transfer": 0,
        "liability": 0,
    }
    assert row["history_complete"] is True
    assert row["actor"] == _SESSION_EMAIL
    assert row["actor"] != _OTHER_ACTOR
    assert row["created_at"] is not None
    assert row["size_bytes"] == len(resp.content)
    assert base64.b64decode(row["xlsx_b64"]) == resp.content
    assert "TEST-1001" not in str({k: v for k, v in row.items() if k != "xlsx_b64"})


@patch("dashboard.routers.reports.get_db")
def test_history_complete_is_read_from_the_pack(mock_get_db, reports_app, session_secret):
    from dashboard.services.powers_pack import assemble_powers_pack as real_assemble

    async def _force_incomplete(*args, **kwargs):
        contract = await real_assemble(*args, **kwargs)
        # This fixture has no legacy transfer row, so the pack computes true.
        # Overwrite the built value to prove the archive copies the field.
        assert contract["history_complete"] is True
        contract["history_complete"] = False
        return contract

    db = _db()
    mock_get_db.return_value = db
    client = TestClient(reports_app)
    with patch("dashboard.services.powers_pack.assemble_powers_pack", _force_incomplete):
        resp = client.get(
            "/api/reports/powers-pack/transfer",
            params=_params(surety="osi"),
            cookies=_cookie(),
        )
    assert resp.status_code == 200
    assert resp.headers["x-history-complete"] == "false"
    assert resp.headers["x-transfer-partial"] == "true"
    assert resp.headers["cache-control"] == "no-store"
    row = db["generated_reports"].docs[0]
    assert row["history_complete"] is False
    assert row["pack"] == "transfer"
    assert row["surety_id"] == "osi"


@patch("dashboard.routers.reports.get_db")
def test_failed_build_inserts_nothing(mock_get_db, reports_app, session_secret):
    db = _db()
    mock_get_db.return_value = db
    client = TestClient(reports_app)

    unknown = client.get(
        "/api/reports/powers-pack/combined",
        params={"surety": "acme", "actor": _OTHER_ACTOR},
        headers=_spoof_headers(),
        cookies=_cookie(),
    )
    assert unknown.status_code == 400
    assert unknown.json()["error"] == "unsupported_surety"

    inactive = client.get(
        "/api/reports/powers-pack/execution",
        params={"surety": "lexington"},
        cookies=_cookie(),
    )
    assert inactive.status_code == 400
    assert inactive.json()["error"] == "surety_inactive"

    bad_pack = client.get(
        "/api/reports/powers-pack/bordereau",
        params={"surety": "osi"},
        cookies=_cookie(),
    )
    assert bad_pack.status_code == 400
    assert bad_pack.json()["error"] == "invalid_pack"
    mock_get_db.assert_not_called()
    assert db.get("generated_reports") is None

    with patch(
        "dashboard.services.powers_pack.assemble_powers_pack",
        side_effect=RuntimeError("TEST-build-failed"),
    ):
        failed = client.get(
            "/api/reports/powers-pack/combined",
            params=_params(),
            cookies=_cookie(),
        )
    assert failed.status_code == 500
    assert failed.json()["error_type"] == "RuntimeError"
    assert db.get("generated_reports") is None


@patch("dashboard.routers.reports.get_db")
def test_list_generated_reports_returns_the_pack(mock_get_db, reports_app, session_secret):
    db = _db()
    mock_get_db.return_value = db
    client = TestClient(reports_app)
    first = client.get(
        "/api/reports/powers-pack/combined",
        params=_params(),
        headers=_spoof_headers(),
        cookies=_cookie(),
    )
    assert first.status_code == 200

    listed = client.get("/api/reports/generated")
    assert listed.status_code == 200
    body = listed.json()
    assert body["success"] is True
    assert body["count"] == 1
    report = body["reports"][0]
    assert report["report_type"] == "powers_pack"
    assert report["surety_id"] == "palmetto"
    assert report["pack"] == "combined"
    assert report["filename"] == "Palmetto_Combined_Powers_2026-09-30.xlsx"
    assert report["start_date"] == "2026-09-01"
    assert report["end_date"] == "2026-09-30"
    assert report["counts"]["execution"] == 1
    assert report["history_complete"] is True
    assert report["actor"] == _SESSION_EMAIL
    assert report["has_file"] is True
    assert report["id"]
    assert "xlsx_b64" not in report
    assert "xlsx_base64" not in report

    again = client.get(f"/api/reports/generated/{report['id']}/download")
    assert again.status_code == 200
    assert again.content == first.content

    second = client.get(
        "/api/reports/powers-pack/combined",
        params=_params(),
        cookies=_cookie(),
    )
    assert second.status_code == 200
    assert second.content == first.content
    assert len(db["generated_reports"].docs) == 2
    assert base64.b64decode(db["generated_reports"].docs[1]["xlsx_b64"]) == first.content


@patch("dashboard.routers.reports.get_db")
def test_actor_comes_from_the_session(mock_get_db, reports_app, session_secret):
    db = _db()
    mock_get_db.return_value = db
    client = TestClient(reports_app)

    emailed = client.get(
        "/api/reports/powers-pack/void",
        params=_params(fmt="json"),
        headers=_spoof_headers(),
        cookies=_cookie(email="TEST-Staff@Example.Invalid", agent_name="TEST-Other-Name"),
    )
    assert emailed.status_code == 200
    payload = emailed.json()
    assert payload["surety_id"] == "palmetto"
    assert "actor" not in payload
    assert "xlsx_b64" not in payload
    assert len(db["generated_reports"].docs) == 1
    assert db["generated_reports"].docs[0]["actor"] == "test-staff@example.invalid"
    assert db["generated_reports"].docs[0]["pack"] == "void"
    assert db["generated_reports"].docs[0]["xlsx_b64"]

    named = client.get(
        "/api/reports/powers-pack/execution",
        params={"surety": "osi", "actor": _OTHER_ACTOR, "start_date": "2026-09-01", "end_date": "2026-09-30"},
        headers=_spoof_headers(),
        cookies=_name_only_cookie("TEST-Name-Only"),
    )
    assert named.status_code == 200
    assert db["generated_reports"].docs[1]["actor"] == "TEST-Name-Only"
    assert db["generated_reports"].docs[1]["actor"] != _OTHER_ACTOR

    unsigned = client.get(
        "/api/reports/powers-pack/execution",
        params={"surety": "osi", "actor": _OTHER_ACTOR},
        headers=_spoof_headers(),
    )
    assert unsigned.status_code == 200
    assert db["generated_reports"].docs[2]["actor"] == ""
