"""Fail-closed ID scan / staff attestation gate on Write Bond.

No Mongo, DocuSeal, or network. Names are samples. ID numbers are last-4 only.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.auth.pin_middleware import COOKIE_NAME, _sign_token
from dashboard.services.bond_packet_start import BondPacketStartError, start_indemnitor_bond_packet
from dashboard.services.docuseal_service import DocuSealService
from dashboard.services.identity_verification_service import (
    names_match,
    require_verified_indemnitors,
)

PRIMARY = "Jamie Sample"
OTHER = "Robin Sample"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "ci-not-a-real-secret")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_OSI", "1")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_PALMETTO", "5")
    monkeypatch.setenv("DOCUSEAL_URL", "https://sign.example.invalid")
    monkeypatch.setenv("DOCUSEAL_API_KEY", "test-not-a-real-key")
    monkeypatch.delenv("STAFF_TEST_CASE_MODE", raising=False)


def _cookie(role="staff", email="office@example.invalid", name="Office Staff"):
    token = _sign_token(
        email=email,
        role=role,
        agent_name=name,
        is_admin=role in ("god_admin", "admin"),
    )
    return {COOKIE_NAME: token}


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    async def to_list(self, length=None):
        return list(self._docs)


class _Store:
    def __init__(self, docs=None):
        self.docs = [dict(doc) for doc in (docs or [])]

    def find(self, query=None, projection=None):
        return _Cursor(self.docs)

    async def find_one(self, query=None, projection=None):
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return None


def _collections(seed=None):
    stores = {name: _Store(docs) for name, docs in (seed or {}).items()}

    def get(name):
        if name not in stores:
            stores[name] = _Store()
        return stores[name]

    return stores, get


def _scan(name, *, success=True, state="NC"):
    first, last = name.split()[0], name.split()[-1]
    return {
        "success": success,
        "extracted": {
            "full_name": name,
            "first_name": first,
            "last_name": last,
            "dl_state": state,
        },
    }


def _bound(surety, name=PRIMARY):
    return {
        "bond_case_id": "BC-1",
        "match_id": "M-1",
        "defendant_id": "D-1",
        "indemnitor_id": "I-1",
        "case_number": "26CF1",
        "poa_number": "POA1",
        "booking_number": "BK-1",
        "match_status": "validated",
        "surety_id": surety,
        "bond_amount": 5000,
        "indemnitor_name": name,
        "indemnitor": {"name": name, "email": "signer@example.invalid"},
        "defendant": {"name": "Casey Sample", "email": "casey@example.invalid"},
        "indemnitors": [{"name": name, "email": "signer@example.invalid", "indemnitor_id": "I-1"}],
    }


def _patch_db(get):
    return patch("dashboard.extensions.get_collection", side_effect=get)


class _DocuSeal:
    def __init__(self):
        self.calls = []
        self.is_configured = True

    async def create_submission_for_packet(self, **kwargs):
        raise AssertionError("docuseal touched")


@pytest.mark.parametrize("surety", ["osi", "palmetto"])
@pytest.mark.asyncio
async def test_verified_scan_passes_for_both_sureties(surety):
    stores, get = _collections({
        "indemnitors": [{
            "indemnitor_id": "I-1",
            "booking_number": "BK-1",
            "bond_case_id": "BC-1",
            "id_scan": _scan("Jamie Ann Sample", state="TX" if surety == "palmetto" else "FL"),
        }],
    })
    fake = _DocuSeal()

    async def _create(self, **kwargs):
        fake.calls.append(kwargs)
        return {"submission_id": 1, "submitters": []}

    fake.create_submission_for_packet = _create.__get__(fake, _DocuSeal)
    with _patch_db(get):
        result = await start_indemnitor_bond_packet(
            packet_id="PKT-1",
            surety_id=surety,
            bond_data=_bound(surety),
            poa_record={"max_bond_value": 25000},
            docuseal=fake,
        )
    assert result["surety_id"] == surety
    assert len(fake.calls) == 1
    assert stores["indemnitors"].docs


@pytest.mark.parametrize("surety", ["osi", "palmetto"])
@pytest.mark.asyncio
async def test_failed_scan_refuses_before_docuseal_and_poa(surety):
    _stores, get = _collections({
        "intake_queue": [{
            "booking_number": "BK-1",
            "id_extracted": _scan(PRIMARY, success=False, state="GA"),
        }],
    })
    fake = _DocuSeal()
    poa = AsyncMock(side_effect=AssertionError("poa touched"))
    with _patch_db(get), patch(
        "dashboard.services.bond_packet_start._lookup_assigned_poa",
        poa,
    ):
        with pytest.raises(BondPacketStartError) as raised:
            await start_indemnitor_bond_packet(
                packet_id="PKT-1",
                surety_id=surety,
                bond_data=_bound(surety),
                docuseal=fake,
            )
    assert raised.value.code == "indemnitor_identity_unverified"
    assert "scan failed" in str(raised.value)
    assert PRIMARY in str(raised.value)
    assert fake.calls == []
    poa.assert_not_called()


@pytest.mark.asyncio
async def test_passed_scan_for_a_different_name_refuses():
    _stores, get = _collections({
        "paperwork_packets": [{
            "packet_id": "PKT-1",
            "bond_case_id": "BC-1",
            "id_ocr_role": "indemnitor",
            "id_ocr": _scan(OTHER, state="SC"),
        }],
    })
    with _patch_db(get):
        with pytest.raises(BondPacketStartError) as raised:
            await start_indemnitor_bond_packet(
                packet_id="PKT-1",
                surety_id="osi",
                bond_data=_bound("osi"),
                poa_record={"max_bond_value": 25000},
                docuseal=_DocuSeal(),
            )
    assert raised.value.code == "indemnitor_identity_unverified"
    assert "name mismatch" in str(raised.value)


@pytest.mark.asyncio
async def test_no_scan_and_no_attestation_refuses_before_payload():
    _stores, get = _collections()
    svc = DocuSealService(base_url="https://sign.example.invalid", api_key="test-not-a-real-key")
    svc.create_submission = AsyncMock(side_effect=AssertionError("docuseal touched"))
    with _patch_db(get), patch.object(
        DocuSealService,
        "prefill_values_from_bond",
        side_effect=AssertionError("payload touched"),
    ):
        with pytest.raises(Exception) as raised:
            await svc.create_submission_for_packet(
                template_id=1,
                packet_id="PKT-1",
                bond_data=_bound("palmetto"),
                indemnitors=_bound("palmetto")["indemnitors"],
            )
    assert getattr(raised.value, "code", "") == "indemnitor_identity_unverified"
    assert "no scan and no attestation" in str(raised.value)
    svc.create_submission.assert_not_called()


def test_names_ignore_middle_case_and_punctuation_but_not_last_name():
    assert names_match("jamie ann sample", "Jamie Sample")
    assert names_match("SAMPLE, JAMIE A", "Jamie Sample Jr")
    assert names_match("Jamie O'Neal", "jamie oneal")
    assert not names_match("Jamie O'Neal", "Jamie O'Neill")
    assert not names_match("Jamie Sample", "Robin Sample")
    assert not names_match("Jamie", "Jamie Sample")


@pytest.mark.asyncio
async def test_coindemnitor_must_also_be_verified():
    _stores, get = _collections({
        "indemnitors": [{
            "booking_number": "BK-1",
            "id_scan": _scan(PRIMARY, state="AL"),
        }],
    })
    data = _bound("osi")
    data["indemnitors"] = [
        {"name": PRIMARY, "email": "signer@example.invalid", "indemnitor_id": "I-1"},
        {"name": OTHER, "email": "co@example.invalid", "role": "co_indemnitor"},
    ]
    with _patch_db(get):
        with pytest.raises(BondPacketStartError) as raised:
            await start_indemnitor_bond_packet(
                packet_id="PKT-1",
                surety_id="osi",
                bond_data=data,
                poa_record={"max_bond_value": 25000},
                docuseal=_DocuSeal(),
            )
    assert "Co-indemnitor Robin Sample: no scan and no attestation." in str(raised.value)


@pytest.mark.asyncio
async def test_synthetic_test_packet_keeps_the_old_path():
    """TEST- / PKT-TEST- staff packets skip the scan gate.

    They never reserve a live power. The binding gate still runs.
    """
    fake = _DocuSeal()

    async def _create(self, **kwargs):
        fake.calls.append(kwargs)
        return {"submission_id": 1, "submitters": []}

    fake.create_submission_for_packet = _create.__get__(fake, _DocuSeal)
    data = _bound("osi")
    data["booking_number"] = "TEST-IDENTITY1"
    data["is_test"] = True
    with patch("dashboard.extensions.get_collection", side_effect=AssertionError("mongo touched")):
        result = await start_indemnitor_bond_packet(
            packet_id="PKT-TEST-IDENTITY1",
            surety_id="osi",
            bond_data=data,
            poa_record={"max_bond_value": 25000},
            docuseal=fake,
            staff_test_case=True,
        )
    assert result["submission"]["submission_id"] == 1
    assert fake.calls


@pytest.mark.asyncio
async def test_staff_test_flag_does_not_exempt_a_real_booking():
    _stores, get = _collections()
    with _patch_db(get):
        with pytest.raises(BondPacketStartError) as raised:
            await start_indemnitor_bond_packet(
                packet_id="PKT-1",
                surety_id="palmetto",
                bond_data=_bound("palmetto"),
                poa_record={"max_bond_value": 25000},
                docuseal=_DocuSeal(),
                staff_test_case=True,
            )
    assert raised.value.code == "indemnitor_identity_unverified"


def _client():
    from dashboard.routers.paperwork import paperwork_bp

    app = FastAPI()
    app.include_router(paperwork_bp)
    return TestClient(app)


def test_attestation_passes_and_stores_last4_from_the_session():
    stores, get = _collections()
    client = _client()
    client.cookies.update(_cookie())
    body = {
        "indemnitor_name": "Jamie Ann Sample",
        "id_type": "drivers_license",
        "issuing_state": "TN",
        "id_last4": "4821",
        "bond_case_id": "BC-1",
        "booking_number": "BK-1",
        "staff_user": "not-the-session@example.invalid",
        "actor": "also-not-the-session@example.invalid",
    }
    with _patch_db(get):
        saved = client.post("/api/paperwork/attest-id", json=body)
        assert saved.status_code == 200, saved.text
        assert saved.json()["attested"] is True
        decision = asyncio.run(require_verified_indemnitors(
            bond_data=_bound("osi"),
            bond_case_id="BC-1",
            booking_number="BK-1",
            packet_id="PKT-1",
        ))
    assert decision["ok"] is True
    assert decision["methods"] == ["attestation"]
    assert len(stores["audit_events"].docs) == 1
    row = stores["audit_events"].docs[0]
    blob = str(row)
    assert row["actor"] == "office@example.invalid"
    assert row["details"]["staff_user"] == "office@example.invalid"
    assert "not-the-session" not in blob
    assert row["details"]["id_last4"] == "4821"
    assert row["details"]["id_type"] == "drivers_license"
    assert row["details"]["issuing_state"] == "TN"
    assert row["timestamp"]
    assert "dl_number" not in row["details"]
    assert "123456789" not in blob


def test_full_id_number_is_rejected_and_stores_nothing():
    stores, get = _collections()
    client = _client()
    client.cookies.update(_cookie(role="admin"))
    with _patch_db(get):
        full = client.post("/api/paperwork/attest-id", json={
            "indemnitor_name": PRIMARY,
            "id_type": "drivers_license",
            "issuing_state": "LA",
            "id_last4": "4821",
            "id_number": "D123456789",
            "bond_case_id": "BC-1",
        })
        long_last4 = client.post("/api/paperwork/attest-id", json={
            "indemnitor_name": PRIMARY,
            "id_type": "state_id",
            "issuing_state": "MS",
            "id_last4": "123456789",
            "booking_number": "BK-1",
        })
    assert full.status_code == 422
    assert full.json()["error"] == "attestation_id_number_rejected"
    assert long_last4.status_code == 422
    assert long_last4.json()["error"] == "attestation_id_number_rejected"
    assert stores.get("audit_events") is None or stores["audit_events"].docs == []
    assert "D123456789" not in str(stores)


def test_recovery_cannot_attest_and_sub_agent_can():
    stores, get = _collections()
    client = _client()
    body = {
        "indemnitor_name": PRIMARY,
        "id_type": "passport",
        "issuing_state": "CT",
        "id_last4": "4821",
        "bond_case_id": "BC-1",
    }
    with _patch_db(get):
        missing = client.post("/api/paperwork/attest-id", json=body)
        assert missing.status_code == 401
        client.cookies.update(_cookie(role="recovery", email="recovery@example.invalid"))
        blocked = client.post("/api/paperwork/attest-id", json=body)
        assert blocked.status_code == 403
        client.cookies.clear()
        client.cookies.update(_cookie(role="sub_agent", email="agent@example.invalid", name="Case Agent"))
        allowed = client.post("/api/paperwork/attest-id", json=body)
    assert allowed.status_code == 200, allowed.text
    assert stores["audit_events"].docs[0]["actor"] == "agent@example.invalid"
    assert len(stores["audit_events"].docs) == 1


def test_finalize_returns_422_before_poa_or_docuseal():
    from dashboard.routers.paperwork import paperwork_bp

    app = FastAPI()
    app.include_router(paperwork_bp)
    client = TestClient(app)
    client.cookies.update(_cookie(role="god_admin"))
    stores, get = _collections()
    ctx = {
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
    poa_calls = {"n": 0}

    def collections(name):
        if name == "poa_inventory":
            poa_calls["n"] += 1
            raise AssertionError("poa touched")
        return get(name)

    svc = DocuSealService(base_url="https://sign.example.invalid", api_key="test-not-a-real-key")
    svc.create_submission = AsyncMock(side_effect=AssertionError("docuseal touched"))
    with patch("dashboard.services.packet_builder_service.resolve_case_context", new=AsyncMock(return_value=ctx)), \
         patch("dashboard.services.packet_builder_service.resolve_client_esign_provider", new=AsyncMock(return_value="docuseal")), \
         patch("dashboard.routers.helpers.reject_unless_write_book", new=AsyncMock(return_value=None)), \
         patch("dashboard.routers.paperwork.get_collection", side_effect=collections), \
         patch("dashboard.extensions.get_collection", side_effect=get), \
         patch("dashboard.services.docuseal_service.get_docuseal_service", return_value=svc):
        response = client.post("/api/paperwork/packet/finalize", json={"surety_id": "palmetto", "packet_id": "PKT-1"})
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "indemnitor_identity_unverified"
    assert "Jamie Sample" in body["message"]
    assert "no scan and no attestation" in body["message"]
    assert poa_calls["n"] == 0
    svc.create_submission.assert_not_called()
    assert "4821" not in response.text


def _defendant_scan(name, *, success=True, state="FL"):
    return {
        "packet_id": "PKT-1",
        "bond_case_id": "BC-1",
        "booking_number": "BK-1",
        "id_ocr_role": "defendant",
        "id_ocr": _scan(name, success=success, state=state),
    }


@pytest.mark.parametrize("surety", ["osi", "palmetto"])
@pytest.mark.asyncio
async def test_self_indemnitor_passed_defendant_scan_passes_for_both_sureties(surety):
    _stores, get = _collections({
        "paperwork_packets": [_defendant_scan("Jamie Ann Sample", state="TX" if surety == "palmetto" else "FL")],
    })
    data = _bound(surety)
    data["self_indemnitor"] = True
    fake = _DocuSeal()

    async def _create(self, **kwargs):
        fake.calls.append(kwargs)
        return {"submission_id": 1, "submitters": []}

    fake.create_submission_for_packet = _create.__get__(fake, _DocuSeal)
    with _patch_db(get):
        result = await start_indemnitor_bond_packet(
            packet_id="PKT-1",
            surety_id=surety,
            bond_data=data,
            poa_record={"max_bond_value": 25000},
            docuseal=fake,
        )
    assert result["surety_id"] == surety
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_self_indemnitor_defendant_scan_different_name_is_name_mismatch():
    _stores, get = _collections({
        "paperwork_packets": [_defendant_scan(OTHER, state="SC")],
    })
    data = _bound("osi")
    data["self_indemnitor"] = True
    with _patch_db(get):
        with pytest.raises(BondPacketStartError) as raised:
            await start_indemnitor_bond_packet(
                packet_id="PKT-1",
                surety_id="osi",
                bond_data=data,
                poa_record={"max_bond_value": 25000},
                docuseal=_DocuSeal(),
            )
    assert raised.value.code == "indemnitor_identity_unverified"
    assert "name mismatch" in str(raised.value)
    assert PRIMARY in str(raised.value)


@pytest.mark.asyncio
async def test_self_indemnitor_failed_defendant_scan_refuses():
    _stores, get = _collections({
        "paperwork_packets": [_defendant_scan(PRIMARY, success=False, state="GA")],
    })
    data = _bound("palmetto")
    data["self_indemnitor"] = True
    fake = _DocuSeal()
    poa = AsyncMock(side_effect=AssertionError("poa touched"))
    with _patch_db(get), patch(
        "dashboard.services.bond_packet_start._lookup_assigned_poa",
        poa,
    ):
        with pytest.raises(BondPacketStartError) as raised:
            await start_indemnitor_bond_packet(
                packet_id="PKT-1",
                surety_id="palmetto",
                bond_data=data,
                docuseal=fake,
            )
    assert raised.value.code == "indemnitor_identity_unverified"
    assert "scan failed" in str(raised.value)
    assert fake.calls == []
    poa.assert_not_called()


@pytest.mark.asyncio
async def test_unmarked_packet_ignores_matching_defendant_scan():
    _stores, get = _collections({
        "paperwork_packets": [_defendant_scan(PRIMARY, state="NC")],
    })
    data = _bound("osi")
    data["self_indemnitor"] = False
    data["indemnitor"]["relationship"] = "Self"
    data["relationship"] = "Self"
    with _patch_db(get):
        with pytest.raises(BondPacketStartError) as raised:
            await start_indemnitor_bond_packet(
                packet_id="PKT-1",
                surety_id="osi",
                bond_data=data,
                poa_record={"max_bond_value": 25000},
                docuseal=_DocuSeal(),
            )
    assert raised.value.code == "indemnitor_identity_unverified"
    assert "no scan and no attestation" in str(raised.value)
    assert "name mismatch" not in str(raised.value)


@pytest.mark.asyncio
async def test_self_indemnitor_defendant_scan_does_not_cover_coindemnitor():
    _stores, get = _collections({
        "paperwork_packets": [_defendant_scan(PRIMARY, state="AL")],
    })
    data = _bound("osi")
    data["self_indemnitor"] = True
    data["indemnitors"] = [
        {"name": PRIMARY, "email": "signer@example.invalid", "indemnitor_id": "I-1"},
        {"name": OTHER, "email": "co@example.invalid", "role": "co_indemnitor"},
    ]
    with _patch_db(get):
        with pytest.raises(BondPacketStartError) as raised:
            await start_indemnitor_bond_packet(
                packet_id="PKT-1",
                surety_id="osi",
                bond_data=data,
                poa_record={"max_bond_value": 25000},
                docuseal=_DocuSeal(),
            )
    assert str(raised.value) == "Co-indemnitor Robin Sample: no scan and no attestation."
