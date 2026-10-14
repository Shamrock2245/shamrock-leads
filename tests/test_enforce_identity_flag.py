"""Opt-in ``enforce_identity`` for staff TEST packets.

The flag can only turn the synthetic identity skip off. Mongo and DocuSeal
are stubs. Names are samples. No live power is reserved.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.services.bond_packet_start import BondPacketStartError, start_indemnitor_bond_packet
from dashboard.services.docuseal_service import DocuSealService
from dashboard.services.identity_verification_service import (
    enforce_identity_opt_in,
    synthetic_staff_test_exempt,
)
from tests.test_staff_test_case_write_bond import (
    _client,
    _cookie,
    _docuseal_patches,
    _service,
    _stores,
)

PRIMARY = "Jamie Sample"
BOOKING = "TEST-SMOKE1"
PACKET = "PKT-TEST-SMOKE1"
CASE_ID = "TEST-BOND-SMOKE1"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "ci-not-a-real-secret")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_OSI", "1")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_PALMETTO", "5")
    monkeypatch.setenv("DOCUSEAL_URL", "https://sign.example.invalid")
    monkeypatch.setenv("DOCUSEAL_API_KEY", "test-not-a-real-key")
    monkeypatch.delenv("STAFF_TEST_CASE_MODE", raising=False)


def _scan():
    return {
        "success": True,
        "extracted": {
            "full_name": PRIMARY,
            "first_name": "Jamie",
            "last_name": "Sample",
            "dl_state": "FL",
        },
    }


def _indemnitor_doc():
    return {
        "indemnitor_id": "TEST-IND-SMOKE1",
        "booking_number": BOOKING,
        "bond_case_id": CASE_ID,
        "packet_id": PACKET,
        "name": PRIMARY,
        "id_scan": _scan(),
    }


def _test_packet(**extra):
    doc = {
        "packet_id": PACKET,
        "is_test": True,
        "test_case": True,
        "booking_number": BOOKING,
        "bond_case_id": CASE_ID,
        "match_id": "TEST-MATCH-SMOKE1",
        "match_status": "validated",
        "defendant_id": "TEST-DEF-SMOKE1",
        "indemnitor_id": "TEST-IND-SMOKE1",
        "case_number": "TEST-CASE-SMOKE1",
        "poa_number": "TEST-POA-0001",
        "surety_id": "osi",
        "bond_amount": 5000,
        "county": "Lee",
        "state": "FL",
        "defendant_name": "Casey Sample",
        "indemnitor_name": PRIMARY,
        "indemnitor": {"name": PRIMARY, "email": "signer@example.invalid"},
        "defendant": {"name": "Casey Sample", "email": "casey@example.invalid"},
        "indemnitors": [{"name": PRIMARY, "email": "signer@example.invalid", "indemnitor_id": "TEST-IND-SMOKE1"}],
    }
    doc.update(extra)
    return doc


def _real_packet():
    doc = _test_packet()
    doc.update({
        "packet_id": "PKT-1",
        "is_test": False,
        "test_case": False,
        "booking_number": "BK-1",
        "bond_case_id": "BC-1",
        "match_id": "M-1",
        "defendant_id": "D-1",
        "indemnitor_id": "I-1",
        "case_number": "26CF1",
        "poa_number": "POA1",
    })
    return doc


def _parties():
    return {
        "indemnitors": [{"name": PRIMARY, "email": "signer@example.invalid", "indemnitor_id": "TEST-IND-SMOKE1"}],
        "defendant": {"name": "Casey Sample", "email": "casey@example.invalid"},
    }


def _poa_touched(stores) -> bool:
    poa = stores.get("poa_inventory")
    if poa is None:
        return False
    return bool(poa.finds or poa.updates or poa.inserts)


def _push_client():
    from dashboard.routers.paperwork import paperwork_bp

    app = FastAPI()
    app.include_router(paperwork_bp)
    return TestClient(app)


def _post_push(client, stores, captured, packet_id, body):
    svc = _service(captured)
    _, collections = stores if isinstance(stores, tuple) else (None, stores)
    if not callable(collections):
        _stored, collections = stores
    with patch("dashboard.routers.paperwork.get_collection", side_effect=collections), \
         patch("dashboard.extensions.get_collection", side_effect=collections), \
         patch("dashboard.services.docuseal_service.get_docuseal_service", return_value=svc):
        return client.post(f"/api/paperwork/{packet_id}/docuseal", json=body)


def _finalize(client, stores, captured, body):
    svc = _service(captured)
    _stored, collections = stores
    delivery = AsyncMock()
    payment = AsyncMock()
    ensure = AsyncMock()
    patches = _docuseal_patches(captured, collections, delivery, payment, ensure, svc)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
         patch("dashboard.routers.helpers.reject_unless_write_book", new=AsyncMock(return_value=None)):
        return client.post("/api/paperwork/packet/finalize", json=body)


def _test_body(**extra):
    body = {
        "test_case": True,
        "surety_id": "osi",
        "booking_number": BOOKING,
        "case_number": "TEST-CASE-SMOKE1",
        "packet_id": PACKET,
        "defendant_name": "Casey Sample",
        "indemnitor_name": PRIMARY,
        "bond_amount": 5000,
        "county": "Lee",
        "state": "FL",
    }
    body.update(extra)
    return body


def _real_ctx():
    return {
        "defendant": {"name": "Casey Sample", "email": "casey@example.invalid"},
        "indemnitor": {"name": PRIMARY, "email": "signer@example.invalid"},
        "county": "Lee",
        "state": "FL",
        "bond_amount": 5000,
        "match_status": "validated",
        "bond_case_id": "BC-1",
        "match_id": "M-1",
        "defendant_id": "D-1",
        "indemnitor_id": "I-1",
        "case_number": "26CF1",
        "poa_number": "POA1",
        "booking_number": "BK-1",
        "surety_id": "osi",
    }


def _assert_refused(response, captured, stores):
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "indemnitor_identity_unverified"
    assert "no scan and no attestation" in body["message"]
    assert "kwargs" not in captured
    assert _poa_touched(stores) is False


def test_opt_in_accepts_only_an_explicit_true():
    assert enforce_identity_opt_in(True) is True
    assert enforce_identity_opt_in("true") is True
    assert enforce_identity_opt_in(False) is False
    assert enforce_identity_opt_in("false") is False
    assert enforce_identity_opt_in(None) is False
    assert enforce_identity_opt_in(0) is False


def test_exemption_turns_off_only_for_a_staff_test_opt_in(monkeypatch):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    common = {
        "staff_test_case": True,
        "booking_number": BOOKING,
        "packet_id": PACKET,
    }
    assert synthetic_staff_test_exempt(**common) is True
    assert synthetic_staff_test_exempt(**common, enforce_identity=False, staff_authenticated=True) is True
    assert synthetic_staff_test_exempt(**common, enforce_identity=True, staff_authenticated=False) is True
    assert synthetic_staff_test_exempt(
        **common, enforce_identity=True, staff_authenticated=True,
    ) is False
    assert synthetic_staff_test_exempt(
        staff_test_case=True,
        booking_number="BK-1",
        packet_id="PKT-1",
        enforce_identity=True,
        staff_authenticated=True,
    ) is False
    monkeypatch.delenv("STAFF_TEST_CASE_MODE", raising=False)
    assert synthetic_staff_test_exempt(
        **common, enforce_identity=True, staff_authenticated=True,
    ) is True


def _bond(booking=BOOKING, packet_case=CASE_ID):
    return {
        "bond_case_id": packet_case,
        "match_id": "TEST-MATCH-SMOKE1",
        "defendant_id": "TEST-DEF-SMOKE1",
        "indemnitor_id": "TEST-IND-SMOKE1",
        "case_number": "TEST-CASE-SMOKE1",
        "poa_number": "TEST-POA-0001",
        "booking_number": booking,
        "match_status": "validated",
        "surety_id": "osi",
        "bond_amount": 5000,
        "county": "Lee",
        "indemnitor_name": PRIMARY,
        "indemnitor": {"name": PRIMARY, "email": "signer@example.invalid"},
        "defendant": {"name": "Casey Sample", "email": "casey@example.invalid"},
        "indemnitors": [{"name": PRIMARY, "email": "signer@example.invalid", "indemnitor_id": "TEST-IND-SMOKE1"}],
    }


@pytest.mark.asyncio
async def test_write_bond_start_refuses_before_docuseal_and_poa(monkeypatch):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    captured = {}
    svc = _service(captured)
    poa = AsyncMock(side_effect=AssertionError("poa reserved"))
    stores, collections = _stores()
    with patch("dashboard.extensions.get_collection", side_effect=collections), \
         patch("dashboard.services.bond_packet_start._lookup_assigned_poa", poa):
        with pytest.raises(BondPacketStartError) as raised:
            await start_indemnitor_bond_packet(
                packet_id=PACKET,
                surety_id="osi",
                bond_data=_bond(),
                poa_record={"max_bond_value": 25000, "is_test": True},
                docuseal=svc,
                staff_test_case=True,
                enforce_identity=True,
                staff_authenticated=True,
            )
    assert raised.value.code == "indemnitor_identity_unverified"
    assert "kwargs" not in captured
    poa.assert_not_called()
    assert _poa_touched(stores) is False


@pytest.mark.asyncio
async def test_write_bond_start_without_the_flag_stays_exempt(monkeypatch):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    captured = {}
    svc = _service(captured)
    with patch("dashboard.extensions.get_collection", side_effect=AssertionError("mongo touched")):
        result = await start_indemnitor_bond_packet(
            packet_id=PACKET,
            surety_id="osi",
            bond_data=_bond(),
            poa_record={"max_bond_value": 25000, "is_test": True},
            docuseal=svc,
            staff_test_case=True,
            staff_authenticated=True,
        )
    assert result["submission"]["submission_id"] == 44
    assert "kwargs" in captured


@pytest.mark.asyncio
async def test_write_bond_start_with_flag_and_scan_proceeds(monkeypatch):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    captured = {}
    svc = _service(captured)
    stores, collections = _stores({"indemnitors": [_indemnitor_doc()]})
    with patch("dashboard.extensions.get_collection", side_effect=collections):
        result = await start_indemnitor_bond_packet(
            packet_id=PACKET,
            surety_id="osi",
            bond_data=_bond(),
            poa_record={"max_bond_value": 25000, "is_test": True},
            docuseal=svc,
            staff_test_case=True,
            enforce_identity=True,
            staff_authenticated=True,
        )
    assert result["submission"]["submission_id"] == 44
    assert _poa_touched(stores) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [False, None])
async def test_write_bond_start_real_booking_stays_gated(monkeypatch, flag):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    captured = {}
    svc = _service(captured)
    poa = AsyncMock(side_effect=AssertionError("poa reserved"))
    kwargs = {
        "packet_id": "PKT-1",
        "surety_id": "osi",
        "bond_data": _bond(booking="BK-1", packet_case="BC-1"),
        "poa_record": {"max_bond_value": 25000},
        "docuseal": svc,
        "staff_test_case": True,
        "staff_authenticated": True,
    }
    if flag is not None:
        kwargs["enforce_identity"] = flag
    stores, collections = _stores()
    with patch("dashboard.extensions.get_collection", side_effect=collections), \
         patch("dashboard.services.bond_packet_start._lookup_assigned_poa", poa):
        with pytest.raises(BondPacketStartError) as raised:
            await start_indemnitor_bond_packet(**kwargs)
    assert raised.value.code == "indemnitor_identity_unverified"
    assert "kwargs" not in captured
    poa.assert_not_called()
    assert _poa_touched(stores) is False


@pytest.mark.asyncio
async def test_write_bond_start_ignores_the_flag_when_not_staff_or_mode_off(monkeypatch):
    captured = {}
    svc = _service(captured)
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    with patch("dashboard.extensions.get_collection", side_effect=AssertionError("mongo touched")):
        skipped = await start_indemnitor_bond_packet(
            packet_id=PACKET,
            surety_id="osi",
            bond_data=_bond(),
            poa_record={"max_bond_value": 25000, "is_test": True},
            docuseal=svc,
            staff_test_case=True,
            enforce_identity=True,
            staff_authenticated=False,
        )
    assert skipped["submission"]["submission_id"] == 44

    captured.clear()
    monkeypatch.delenv("STAFF_TEST_CASE_MODE", raising=False)
    with patch("dashboard.extensions.get_collection", side_effect=AssertionError("mongo touched")):
        mode_off = await start_indemnitor_bond_packet(
            packet_id=PACKET,
            surety_id="osi",
            bond_data=_bond(),
            poa_record={"max_bond_value": 25000, "is_test": True},
            docuseal=svc,
            staff_test_case=True,
            enforce_identity=True,
            staff_authenticated=True,
        )
    assert mode_off["submission"]["submission_id"] == 44


def test_docuseal_push_staff_test_with_flag_returns_422(monkeypatch):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    stores, collections = _stores({"paperwork_packets": [_test_packet()]})
    captured = {}
    client = _push_client()
    client.cookies.update(_cookie("staff"))
    response = _post_push(client, (stores, collections), captured, PACKET, {"enforce_identity": True, **_parties()})
    _assert_refused(response, captured, stores)


def test_docuseal_push_without_the_flag_stays_exempt(monkeypatch):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    stores, collections = _stores({"paperwork_packets": [_test_packet()]})
    captured = {}
    client = _push_client()
    client.cookies.update(_cookie("admin"))
    response = _post_push(client, (stores, collections), captured, PACKET, _parties())
    assert response.status_code == 200, response.text
    assert captured["kwargs"]["submitters"]
    assert _poa_touched(stores) is False

    captured.clear()
    response = _post_push(
        client, (stores, collections), captured, PACKET,
        {"enforce_identity": False, **_parties()},
    )
    assert response.status_code == 200, response.text
    assert "kwargs" in captured


def test_docuseal_push_with_flag_and_scan_proceeds(monkeypatch):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    stores, collections = _stores({
        "paperwork_packets": [_test_packet()],
        "indemnitors": [_indemnitor_doc()],
    })
    captured = {}
    client = _push_client()
    client.cookies.update(_cookie("god_admin"))
    response = _post_push(
        client, (stores, collections), captured, PACKET,
        {"enforce_identity": True, **_parties()},
    )
    assert response.status_code == 200, response.text
    assert captured["kwargs"]["submitters"]
    assert _poa_touched(stores) is False


def test_docuseal_push_real_booking_stays_gated(monkeypatch):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    packet = _real_packet()
    stores, collections = _stores({"paperwork_packets": [packet]})
    client = _push_client()
    client.cookies.update(_cookie("staff"))
    for body in ({}, {"enforce_identity": False}, {"enforce_identity": True}):
        captured = {}
        response = _post_push(client, (stores, collections), captured, "PKT-1", {**body, **_parties()})
        _assert_refused(response, captured, stores)


def test_docuseal_push_ignores_the_flag_for_non_staff_and_mode_off(monkeypatch):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    stores, collections = _stores({"paperwork_packets": [_test_packet()]})
    client = _push_client()
    captured = {}
    missing = _post_push(client, (stores, collections), captured, PACKET, {"enforce_identity": True, **_parties()})
    _assert_refused(missing, captured, stores)

    client.cookies.update(_cookie("sub_agent", "Sample Agent", "W000000"))
    captured = {}
    sub = _post_push(client, (stores, collections), captured, PACKET, {"enforce_identity": True, **_parties()})
    _assert_refused(sub, captured, stores)

    captured = {}
    unchanged = _post_push(client, (stores, collections), captured, PACKET, _parties())
    _assert_refused(unchanged, captured, stores)

    monkeypatch.delenv("STAFF_TEST_CASE_MODE", raising=False)
    client.cookies.update(_cookie("staff"))
    for body in (_parties(), {"enforce_identity": True, **_parties()}):
        captured = {}
        response = _post_push(client, (stores, collections), captured, PACKET, body)
        _assert_refused(response, captured, stores)


def test_finalize_staff_test_with_flag_returns_422(monkeypatch):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    stores, collections = _stores()
    captured = {}
    client = _client()
    client.cookies.update(_cookie("staff"))
    response = _finalize(client, (stores, collections), captured, _test_body(enforce_identity=True))
    _assert_refused(response, captured, stores)


def test_finalize_without_the_flag_stays_exempt(monkeypatch):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    client = _client()
    client.cookies.update(_cookie("admin"))
    for body in (_test_body(), _test_body(enforce_identity=False)):
        stores, collections = _stores()
        captured = {}
        response = _finalize(client, (stores, collections), captured, body)
        assert response.status_code == 200, response.text
        assert "kwargs" in captured
        assert _poa_touched(stores) is False


def test_finalize_with_flag_and_scan_proceeds(monkeypatch):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    stores, collections = _stores({"indemnitors": [_indemnitor_doc()]})
    captured = {}
    client = _client()
    client.cookies.update(_cookie("god_admin"))
    response = _finalize(
        client, (stores, collections), captured, _test_body(enforce_identity=True),
    )
    assert response.status_code == 200, response.text
    assert "kwargs" in captured
    assert _poa_touched(stores) is False


def test_finalize_real_booking_stays_gated(monkeypatch):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    stores, collections = _stores()
    client = _client()
    client.cookies.update(_cookie("staff"))
    ctx = _real_ctx()
    for body in (
        {"surety_id": "osi", "packet_id": "PKT-1"},
        {"surety_id": "osi", "packet_id": "PKT-1", "enforce_identity": False},
        {"surety_id": "osi", "packet_id": "PKT-1", "enforce_identity": True},
    ):
        captured = {}
        svc = _service(captured)
        with patch("dashboard.services.packet_builder_service.resolve_case_context", new=AsyncMock(return_value=ctx)), \
             patch("dashboard.services.packet_builder_service.resolve_client_esign_provider", new=AsyncMock(return_value="docuseal")), \
             patch("dashboard.routers.helpers.reject_unless_write_book", new=AsyncMock(return_value=None)), \
             patch("dashboard.routers.paperwork.get_collection", side_effect=collections), \
             patch("dashboard.extensions.get_collection", side_effect=collections), \
             patch("dashboard.services.docuseal_service.get_docuseal_service", return_value=svc):
            response = client.post("/api/paperwork/packet/finalize", json=body)
        _assert_refused(response, captured, stores)


def test_finalize_ignores_the_flag_for_non_staff_and_mode_off(monkeypatch):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    captured = {}
    client = _client()
    missing = client.post("/api/paperwork/packet/finalize", json=_test_body(enforce_identity=True))
    assert missing.status_code == 401
    assert missing.json()["error"] == "staff_session_required"
    assert "kwargs" not in captured

    same = client.post("/api/paperwork/packet/finalize", json=_test_body())
    assert same.status_code == 401
    assert same.json()["error"] == "staff_session_required"

    client.cookies.update(_cookie("sub_agent", "Sample Agent", "W000000"))
    sub = client.post("/api/paperwork/packet/finalize", json=_test_body(enforce_identity=True))
    assert sub.status_code == 401
    assert sub.json()["error"] == "staff_session_required"

    monkeypatch.delenv("STAFF_TEST_CASE_MODE", raising=False)
    client.cookies.update(_cookie("staff"))
    disabled = client.post("/api/paperwork/packet/finalize", json=_test_body(enforce_identity=True))
    assert disabled.status_code == 403
    assert disabled.json()["error"] == "staff_test_case_disabled"
    plain = client.post("/api/paperwork/packet/finalize", json=_test_body())
    assert plain.status_code == 403
    assert plain.json()["error"] == "staff_test_case_disabled"


@pytest.mark.asyncio
async def test_create_submission_for_packet_refuses_before_the_http_call(monkeypatch):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    from dashboard.services.identity_verification_service import IndemnitorIdentityError

    svc = DocuSealService(base_url="https://sign.example.invalid", api_key="test-not-a-real-key")
    svc.create_submission = AsyncMock(side_effect=AssertionError("docuseal"))
    stores, collections = _stores()
    with patch("dashboard.extensions.get_collection", side_effect=collections):
        with pytest.raises(IndemnitorIdentityError):
            await svc.create_submission_for_packet(
                template_id=1,
                packet_id=PACKET,
                bond_data=_bond(),
                staff_test_case=True,
                enforce_identity=True,
                staff_authenticated=True,
            )
    svc.create_submission.assert_not_called()
    assert _poa_touched(stores) is False
