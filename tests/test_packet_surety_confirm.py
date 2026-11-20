"""Super CRM surety confirmation for packets (chat-agent packets stage with no surety).

Covers POST /api/paperwork/packets/{packet_id}/confirm-surety, the DocuSeal push
using the confirmed surety (DocuSeal mocked; nothing is sent), and the packet
screen picker logic in sl-paperwork.js (node sim in tests/js).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.bb_fake_mongo import FakeDB  # noqa: E402


def _agent_packet(**extra):
    doc = {
        "packet_id": "PKT-AGENTTEST1",
        "intake_id": "INT-AGT-TEST1",
        "status": "staged",
        "staged_by_agent": True,
        "surety_id": None,
        "template": None,
        "docuseal_submission_id": None,
        "voided": False,
        "defendant_name": "Test Person",
        "defendant_booking_number": "TEST-0001",
    }
    doc.update(extra)
    return doc


def _walkin_packet(**extra):
    doc = {
        "packet_id": "PKT-WALKIN1",
        "intake_id": "INT-WALKIN1",
        "status": "draft",
        "surety_id": "osi",
        "template": "osi",
        "docuseal_submission_id": None,
        "updated_at": "2026-11-07T00:00:00+00:00",
    }
    doc.update(extra)
    return doc


@pytest.fixture
def env(monkeypatch):
    from dashboard.routers import paperwork as paperwork_mod

    db = FakeDB()
    monkeypatch.setattr(paperwork_mod, "get_collection", db.get_collection)
    monkeypatch.setattr("dashboard.extensions.get_collection", db.get_collection)
    monkeypatch.setattr("dashboard.services.surety_registry._published_doc", lambda sid: None)
    app = FastAPI()
    app.include_router(paperwork_mod.paperwork_bp)
    return SimpleNamespace(db=db, client=TestClient(app), mod=paperwork_mod)


def _seed(env, doc):
    env.db["paperwork_packets"].docs.append(dict(doc))


def _confirm(env, packet_id, body):
    return env.client.post(f"/api/paperwork/packets/{packet_id}/confirm-surety", json=body)


def _events(env):
    return [e for e in env.db["audit_events"].docs if e.get("event_type") == "packet_surety_confirmed"]


@pytest.mark.parametrize("raw,expected", [("osi", "osi"), ("Palmetto", "palmetto"), (" PALMETTO ", "palmetto")])
def test_confirm_persists_surety_and_audits(env, raw, expected):
    _seed(env, _agent_packet())
    resp = _confirm(env, "PKT-AGENTTEST1", {"surety_id": raw})
    assert resp.status_code == 200, resp.text
    assert resp.json()["surety_id"] == expected
    assert resp.json()["changed"] is True
    (pkt,) = env.db["paperwork_packets"].docs
    assert pkt["surety_id"] == expected and pkt["template"] == expected
    assert pkt["surety_confirmed_by"] and pkt["surety_confirmed_at"]
    assert pkt["surety_confirmed_source"] == "crm_packet_picker"
    (ev,) = _events(env)
    assert ev["packet_id"] == "PKT-AGENTTEST1"
    assert ev["surety_id"] == expected and ev["previous_surety_id"] is None
    assert ev["actor"] and ev["timestamp"] and ev["staged_by_agent"] is True


@pytest.mark.parametrize("raw", ["acme", "lexington", "{'$ne': null}", "osi palmetto"])
def test_unknown_or_inactive_surety_is_422_and_writes_nothing(env, raw):
    _seed(env, _agent_packet())
    resp = _confirm(env, "PKT-AGENTTEST1", {"surety_id": raw})
    assert resp.status_code == 422
    assert resp.json()["code"] == "surety_unsupported"
    assert env.db["paperwork_packets"].docs[0]["surety_id"] is None
    assert _events(env) == []


@pytest.mark.parametrize("body", [{}, {"surety_id": ""}, {"surety_id": None}, {"surety_id": "  "}])
def test_missing_surety_is_422_surety_required(env, body):
    _seed(env, _agent_packet())
    resp = _confirm(env, "PKT-AGENTTEST1", body)
    assert resp.status_code == 422
    assert resp.json()["code"] == "surety_required"
    assert env.db["paperwork_packets"].docs[0]["surety_id"] is None


def test_existing_walkin_osi_is_unchanged(env):
    before = _walkin_packet()
    _seed(env, before)
    resp = _confirm(env, "PKT-WALKIN1", {"surety_id": "OSI"})
    assert resp.status_code == 200
    assert resp.json() == {
        "success": True, "packet_id": "PKT-WALKIN1",
        "surety_id": "osi", "previous_surety_id": "osi", "changed": False,
    }
    assert env.db["paperwork_packets"].docs[0] == before  # no write at all
    assert _events(env) == []


def test_change_of_surety_while_unsent_is_allowed_and_audited(env):
    _seed(env, _walkin_packet())
    resp = _confirm(env, "PKT-WALKIN1", {"surety_id": "palmetto"})
    assert resp.status_code == 200 and resp.json()["changed"] is True
    assert env.db["paperwork_packets"].docs[0]["surety_id"] == "palmetto"
    (ev,) = _events(env)
    assert ev["previous_surety_id"] == "osi" and ev["surety_id"] == "palmetto"


@pytest.mark.parametrize("extra", [
    {"docuseal_submission_id": "sub_1"},
    {"status": "sent"},
    {"status": "signed"},
    {"voided": True},
])
def test_change_refused_once_sent_or_voided(env, extra):
    _seed(env, _walkin_packet(**extra))
    resp = _confirm(env, "PKT-WALKIN1", {"surety_id": "palmetto"})
    assert resp.status_code == 409 and resp.json()["code"] == "surety_locked"
    assert env.db["paperwork_packets"].docs[0]["surety_id"] == "osi"


def test_change_refused_when_poa_belongs_to_other_surety(env, monkeypatch):
    monkeypatch.setattr("dashboard.services.surety_template_store.list_onboarding", lambda: [], raising=False)
    _seed(env, _agent_packet(poa_number="PSC5-TEST-0001"))
    resp = _confirm(env, "PKT-AGENTTEST1", {"surety_id": "osi"})
    assert resp.status_code == 409 and resp.json()["code"] == "surety_poa_mismatch"
    ok = _confirm(env, "PKT-AGENTTEST1", {"surety_id": "palmetto"})
    assert ok.status_code == 200


def test_unknown_packet_is_404(env):
    resp = _confirm(env, "PKT-NOPE", {"surety_id": "osi"})
    assert resp.status_code == 404 and resp.json()["code"] == "packet_not_found"


def test_concurrent_change_is_409_not_lost_update(env):
    _seed(env, _agent_packet())
    coll = env.db["paperwork_packets"]
    real_find_one = coll.find_one

    async def racing_find_one(*a, **k):
        doc = await real_find_one(*a, **k)
        coll.docs[0]["surety_id"] = "osi"  # another staff member confirmed first
        return doc

    coll.find_one = racing_find_one
    resp = _confirm(env, "PKT-AGENTTEST1", {"surety_id": "palmetto"})
    assert resp.status_code == 409 and resp.json()["code"] == "surety_conflict"
    assert coll.docs[0]["surety_id"] == "osi"


# ── DocuSeal push uses the confirmed surety (DocuSeal mocked) ─────────────────


@pytest.fixture
def docuseal_mock(monkeypatch):
    from dashboard.services import docuseal_service, surety_entitlements

    templates = []

    def resolve(surety):
        templates.append(surety)
        return {"osi": 101, "palmetto": 505}.get(surety)

    svc = SimpleNamespace(
        is_configured=True,
        create_submission_for_packet=AsyncMock(return_value={"submission_id": "sub_test", "submitters": []}),
    )
    monkeypatch.setattr(docuseal_service, "resolve_template_id_for_surety", resolve)
    monkeypatch.setattr(docuseal_service, "get_docuseal_service", lambda: svc)
    monkeypatch.setattr(
        docuseal_service, "build_bond_data_from_dashboard",
        lambda ctx, intake_doc, body, surety_id, session: {"surety_id": surety_id},
    )
    monkeypatch.setattr(surety_entitlements, "entitlement_denial", AsyncMock(return_value=None))
    return SimpleNamespace(templates=templates, svc=svc)


def _push(env, packet_id, body=None):
    return env.client.post(f"/api/paperwork/{packet_id}/docuseal", json=body or {})


def test_push_uses_surety_confirmed_from_picker(env, docuseal_mock):
    _seed(env, _agent_packet())
    assert _confirm(env, "PKT-AGENTTEST1", {"surety_id": "palmetto"}).status_code == 200
    resp = _push(env, "PKT-AGENTTEST1")
    assert resp.status_code == 200, resp.text
    assert docuseal_mock.templates == ["palmetto"]
    kwargs = docuseal_mock.svc.create_submission_for_packet.await_args.kwargs
    assert kwargs["template_id"] == 505
    assert kwargs["bond_data"]["surety_id"] == "palmetto"


def test_push_with_picked_surety_persists_and_audits_it(env, docuseal_mock):
    _seed(env, _agent_packet())
    resp = _push(env, "PKT-AGENTTEST1", {"surety_id": "OSI"})
    assert resp.status_code == 200, resp.text
    assert docuseal_mock.templates == ["osi"]
    pkt = env.db["paperwork_packets"].docs[0]
    assert pkt["surety_id"] == "osi" and pkt["surety_confirmed_source"] == "docuseal_push"
    assert [e["surety_id"] for e in _events(env)] == ["osi"]


@pytest.mark.parametrize("body,code", [({}, "surety_required"), ({"surety_id": "acme"}, "surety_unsupported")])
def test_push_agent_packet_fails_closed(env, docuseal_mock, body, code):
    _seed(env, _agent_packet())
    resp = _push(env, "PKT-AGENTTEST1", body)
    assert resp.status_code == 422 and resp.json()["code"] == code
    assert docuseal_mock.templates == []
    docuseal_mock.svc.create_submission_for_packet.assert_not_awaited()


def test_push_walkin_osi_unchanged(env, docuseal_mock):
    _seed(env, _walkin_packet())
    resp = _push(env, "PKT-WALKIN1")
    assert resp.status_code == 200, resp.text
    assert docuseal_mock.templates == ["osi"]
    assert _events(env) == []
    assert "surety_confirmed_by" not in env.db["paperwork_packets"].docs[0]


# ── Packet screen picker (sl-paperwork.js) ────────────────────────────────────


def test_packet_screen_picker_sim():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run(
        [node, str(ROOT / "tests" / "js" / "paperwork_surety_picker_sim.js"), str(ROOT / "dashboard" / "sl-paperwork.js")],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)

    # No surety: blank "Confirm surety" option selected, picker usable, Send disabled.
    u = out["unconfirmed"]
    assert u["options"][0] == "" and u["value"] == ""
    assert u["pickerDisabled"] is False and u["sendDisabled"] is True
    assert out["finalizeBlocked"] is True and out["blankPickNoCall"] is True

    # Picking Palmetto confirms on the server, then Send unlocks and uses it.
    c = out["confirmed"]
    assert c["calls"] == [{"url": "/api/paperwork/packets/PKT-AGENT1/confirm-surety",
                           "body": {"surety_id": "palmetto"}}]
    assert c["options"] == ["osi", "palmetto"] and c["value"] == "palmetto"
    assert c["sendDisabled"] is False and c["pickerDisabled"] is True
    assert out["finalizeSurety"] == "palmetto"

    # Server rejection keeps it unconfirmed and blocked.
    rj = out["rejected"]
    assert rj["value"] == "" and rj["sendDisabled"] is True and rj["ctxSurety"] == ""

    # Walk-in OSI: preselected, no placeholder, no extra click, no confirm call.
    w = out["walkin"]
    assert w["options"] == ["osi", "palmetto"] and w["value"] == "osi"
    assert w["sendDisabled"] is False and w["calls"] == []
    assert out["walkinAfterPick"]["calls"] == [] and out["walkinAfterPick"]["ctxSurety"] == "osi"


def test_picker_markup_is_wired():
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert 'id="pwApSurety" onchange="SLPaperwork.onSuretyPicked(this.value)"' in html


# ── Finalize hook (chat-agent packets only) ───────────────────────────────────


def _request():
    from starlette.requests import Request

    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""})


def test_finalize_hook_persists_picked_surety_for_agent_packet(env):
    import asyncio

    _seed(env, _agent_packet())
    body, ctx = {"packet_id": "PKT-AGENTTEST1", "surety_id": "Palmetto"}, {}
    err = asyncio.run(env.mod._confirm_agent_packet_surety_for_finalize(_request(), body, ctx))
    assert err is None
    assert ctx["surety_id"] == body["surety_id"] == "palmetto"
    assert env.db["paperwork_packets"].docs[0]["surety_confirmed_source"] == "packet_finalize"


def test_finalize_hook_fails_closed_for_agent_packet_without_surety(env):
    import asyncio

    _seed(env, _agent_packet())
    err = asyncio.run(env.mod._confirm_agent_packet_surety_for_finalize(_request(), {"packet_id": "PKT-AGENTTEST1"}, {}))
    assert err.status_code == 422
    assert json.loads(err.body)["code"] == "surety_required"


def test_finalize_hook_ignores_non_agent_packets(env):
    import asyncio

    _seed(env, _walkin_packet())
    body, ctx = {"packet_id": "PKT-WALKIN1", "surety_id": "palmetto"}, {"surety_id": "osi"}
    assert asyncio.run(env.mod._confirm_agent_packet_surety_for_finalize(_request(), body, ctx)) is None
    assert ctx == {"surety_id": "osi"} and body["surety_id"] == "palmetto"
    assert env.db["paperwork_packets"].docs[0]["surety_id"] == "osi"
    assert _events(env) == []
