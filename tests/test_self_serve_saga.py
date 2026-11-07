"""Saga compensation for a self-serve packet. No live DocuSeal call."""
from __future__ import annotations

import pytest

from dashboard.services.paperwork_self_serve import (
    build_self_serve_packet,
    self_serve_packet_id,
)
from dashboard.services.staff_chain_service import compensate_owned_self_serve_case
from tests._inmem_mongo import FakeDB

EMAIL = "admin@shamrockbailbonds.biz"
PHONE = "2395550100"
NAME = "Test Indemnitor"
CASE_ID = "BC-TEST-SAGA"
PERSON_ID = "IND-TEST-SAGA"


def _install(monkeypatch, db):
    def get_collection(name):
        return db[name]

    monkeypatch.setattr("dashboard.extensions.get_collection", get_collection)
    monkeypatch.setattr("dashboard.services.staff_chain_service.get_collection", get_collection)
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_PALMETTO", "505")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_OSI", "101")

    async def _ok(**kwargs):
        return {"ok": True}

    monkeypatch.setattr(
        "dashboard.services.identity_verification_service.require_verified_indemnitors",
        _ok,
    )


def _case(**extra):
    doc = {
        "_id": CASE_ID,
        "bond_case_id": CASE_ID,
        "source": "self_serve",
        "self_serve_saga_owned": True,
        "self_serve_saga_status": "open",
        "surety_id": "palmetto",
        "indemnitor_id": PERSON_ID,
        "status": "pending_owner_review",
    }
    doc.update(extra)
    return doc


def _fields():
    return {
        "indemnitor_name": NAME,
        "indemnitor_dob": "1900-01-01",
        "indemnitor_dl": "TEST-DL-0000",
        "indemnitor_dl_state": "FL",
    }


def _packet_id():
    return self_serve_packet_id(CASE_ID, "indemnitor", PERSON_ID, [])


async def _build():
    return await build_self_serve_packet(
        case=_case(),
        role="indemnitor",
        person_id=PERSON_ID,
        fields=_fields(),
        email=EMAIL,
        phone=PHONE,
        scan_id="SCAN-TEST-SAGA",
    )


def _audits(db, event_type):
    return [row for row in db["audit_events"].docs if row.get("event_type") == event_type]


@pytest.mark.asyncio
async def test_failure_before_provider_compensates_owned_rows_only(monkeypatch):
    db = FakeDB()
    db["bond_cases"].docs.append(_case())
    db["indemnitors"].docs.append({
        "indemnitor_id": PERSON_ID,
        "id_scan_records": [{"scan_id": "SCAN-TEST-SAGA", "success": True}],
    })
    _install(monkeypatch, db)
    calls = {"n": 0}

    async def _create(self, **kwargs):
        calls["n"] += 1
        raise AssertionError("provider")

    async def _boom(_packet_id):
        raise RuntimeError("local_step_failed")

    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.create_submission", _create)
    monkeypatch.setattr("dashboard.services.paperwork_self_serve._before_provider_dispatch", _boom)
    with pytest.raises(RuntimeError, match="local_step_failed"):
        await _build()
    assert calls["n"] == 0
    assert len(db["paperwork_packets"].docs) == 1
    assert db["paperwork_packets"].docs[0]["status"] == "compensated"
    assert db["paperwork_packets"].docs[0]["docuseal_started"] is False
    assert db["bond_cases"].docs[0]["self_serve_saga_status"] == "compensated"
    assert db["bond_cases"].docs[0]["surety_id"] == "palmetto"
    assert db["indemnitors"].docs[0]["id_scan_records"][0]["scan_id"] == "SCAN-TEST-SAGA"
    assert len(_audits(db, "self_serve_saga_compensated")) == 2
    again = await compensate_owned_self_serve_case(CASE_ID)
    assert again is False
    assert len(_audits(db, "self_serve_saga_compensated")) == 2


@pytest.mark.asyncio
async def test_retry_after_compensation_creates_one_submission(monkeypatch):
    db = FakeDB()
    db["bond_cases"].docs.append(_case())
    _install(monkeypatch, db)
    calls = {"n": 0}

    async def _create(self, **kwargs):
        calls["n"] += 1
        return {
            "id": 41,
            "submitters": [
                {"id": 8, "role": "bondsman", "slug": "TEST-BOND", "email": EMAIL, "status": "pending"},
                {
                    "id": 9,
                    "role": "indemnitor",
                    "slug": "TEST-SLUG",
                    "email": EMAIL,
                    "status": "pending",
                    "external_id": f"{_packet_id()}:indemnitor",
                },
            ],
        }

    async def _boom(_packet_id):
        raise RuntimeError("local_step_failed")

    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.create_submission", _create)
    monkeypatch.setattr("dashboard.services.paperwork_self_serve._before_provider_dispatch", _boom)
    with pytest.raises(RuntimeError):
        await _build()
    async def _resume(_packet_id):
        return None

    monkeypatch.setattr("dashboard.services.paperwork_self_serve._before_provider_dispatch", _resume)
    saved = await _build()
    assert saved["signing_link"].endswith("/s/TEST-SLUG")
    assert calls["n"] == 1
    assert len(db["paperwork_packets"].docs) == 1
    assert db["bond_cases"].docs[0]["self_serve_saga_status"] == "open"
    assert len(_audits(db, "self_serve_saga_resumed")) == 1


@pytest.mark.asyncio
async def test_preexisting_case_is_not_compensated(monkeypatch):
    db = FakeDB()
    staff = _case(source="staff", status="active")
    staff.pop("self_serve_saga_owned")
    staff.pop("self_serve_saga_status")
    db["bond_cases"].docs.append(staff)
    _install(monkeypatch, db)

    async def _boom(_packet_id):
        raise RuntimeError("local_step_failed")

    monkeypatch.setattr("dashboard.services.paperwork_self_serve._before_provider_dispatch", _boom)
    with pytest.raises(RuntimeError, match="local_step_failed"):
        await _build()
    assert db["paperwork_packets"].docs[0]["status"] == "compensated"
    assert db["bond_cases"].docs[0]["status"] == "active"
    assert db["bond_cases"].docs[0]["surety_id"] == "palmetto"
    assert _audits(db, "self_serve_saga_compensated")
    assert not any(row.get("entity_type") == "bond_case" for row in _audits(db, "self_serve_saga_compensated"))


@pytest.mark.asyncio
async def test_provider_accept_without_url_recovers_one_submitter(monkeypatch):
    db = FakeDB()
    db["bond_cases"].docs.append(_case())
    _install(monkeypatch, db)
    external_id = f"{_packet_id()}:indemnitor"
    calls = {"n": 0}

    async def _create(self, **kwargs):
        calls["n"] += 1
        raise RuntimeError("response_lost")

    async def _list(self, **kwargs):
        assert kwargs["external_id"] == external_id
        return {"data": [{
            "id": 9,
            "submission_id": 41,
            "role": "indemnitor",
            "slug": "TEST-SLUG",
            "email": EMAIL,
            "status": "pending",
            "external_id": external_id,
        }]}

    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.create_submission", _create)
    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.list_submitters", _list)
    saved = await _build()
    assert saved["recovered"] is True
    assert saved["signing_link"].endswith("/s/TEST-SLUG")
    assert calls["n"] == 1
    assert len(db["paperwork_packets"].docs) == 1
    assert db["paperwork_packets"].docs[0]["docuseal_submission_id"] == 41
    assert db["bond_cases"].docs[0]["self_serve_saga_status"] == "open"
    assert len(_audits(db, "self_serve_saga_recovered")) == 1


@pytest.mark.asyncio
async def test_ambiguous_provider_result_blocks_a_second_submission(monkeypatch):
    db = FakeDB()
    db["bond_cases"].docs.append(_case())
    _install(monkeypatch, db)
    calls = {"n": 0, "lists": 0}

    async def _create(self, **kwargs):
        calls["n"] += 1
        raise RuntimeError("timeout_after_accept")

    async def _list(self, **kwargs):
        calls["lists"] += 1
        return {"data": []}

    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.create_submission", _create)
    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.list_submitters", _list)
    with pytest.raises(RuntimeError, match="recovery_required"):
        await _build()
    with pytest.raises(RuntimeError, match="recovery_required"):
        await _build()
    assert calls["n"] == 1
    assert calls["lists"] == 2
    assert len(db["paperwork_packets"].docs) == 1
    assert db["paperwork_packets"].docs[0]["status"] == "recovery_required"
    assert db["paperwork_packets"].docs[0]["sign_url"] == ""
    assert db["bond_cases"].docs[0]["self_serve_saga_status"] == "open"
    assert len(_audits(db, "self_serve_saga_recovery_required")) == 1


@pytest.mark.asyncio
async def test_crash_after_accept_and_before_url_save_can_be_reconciled(monkeypatch):
    db = FakeDB()
    db["bond_cases"].docs.append(_case())
    _install(monkeypatch, db)
    calls = {"n": 0}
    external_id = f"{_packet_id()}:indemnitor"

    async def _create(self, **kwargs):
        calls["n"] += 1
        return {
            "id": 77,
            "submitters": [{
                "id": 9,
                "submission_id": 77,
                "role": "indemnitor",
                "slug": "TEST-SLUG",
                "email": EMAIL,
                "status": "pending",
                "external_id": external_id,
            }],
        }

    async def _cut(_packet_id):
        raise RuntimeError("crashed_before_url")

    async def _list(self, **kwargs):
        return {"data": [{
            "id": 9,
            "submission_id": 77,
            "role": "indemnitor",
            "slug": "TEST-SLUG",
            "email": EMAIL,
            "status": "pending",
            "external_id": external_id,
        }]}

    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.create_submission", _create)
    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.list_submitters", _list)
    monkeypatch.setattr("dashboard.services.paperwork_self_serve._before_url_save", _cut)
    with pytest.raises(RuntimeError, match="recovery_required"):
        await _build()
    assert calls["n"] == 1
    assert db["paperwork_packets"].docs[0]["docuseal_submission_id"] == 77
    assert db["paperwork_packets"].docs[0]["status"] == "recovery_required"

    async def _noop(_packet_id):
        return None

    monkeypatch.setattr("dashboard.services.paperwork_self_serve._before_url_save", _noop)
    saved = await _build()
    assert saved["recovered"] is True
    assert calls["n"] == 1
    assert db["paperwork_packets"].docs[0]["sign_url"].endswith("/s/TEST-SLUG")
    assert "swipesimple" not in saved["signing_link"].lower()


@pytest.mark.asyncio
async def test_signed_packet_is_not_compensated(monkeypatch):
    db = FakeDB()
    packet_id = _packet_id()
    db["paperwork_packets"].docs.append({
        "_id": packet_id,
        "packet_id": packet_id,
        "saga_owned": True,
        "source": "self_serve",
        "status": "signed",
        "docuseal_status": "completed",
        "docuseal_started": True,
        "sign_url": "https://sign.shamrockbailbonds.biz/s/TEST-SIGNED",
    })
    _install(monkeypatch, db)
    from dashboard.services.paperwork_self_serve import _compensate_unsent_packet

    changed = await _compensate_unsent_packet(packet_id, CASE_ID)
    assert changed is False
    assert db["paperwork_packets"].docs[0]["status"] == "signed"
    assert db["audit_events"].docs == []
