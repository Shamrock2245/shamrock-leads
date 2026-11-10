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


def _open_row(**overrides):
    external_id = f"{_packet_id()}:indemnitor"
    row = {
        "id": 9,
        "submission_id": 41,
        "role": "indemnitor",
        "slug": "TEST-SLUG",
        "email": EMAIL,
        "phone": PHONE,
        "status": "awaiting",
        "external_id": external_id,
    }
    row.update(overrides)
    return row


def _page(rows, *, count=None, nxt=None):
    return {"data": rows, "pagination": {"count": len(rows) if count is None else count, "next": nxt}}


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
        return _page([_open_row()])

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
        return _page([])

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
                "phone": PHONE,
                "status": "awaiting",
                "external_id": external_id,
            }],
        }

    async def _cut(_packet_id):
        raise RuntimeError("crashed_before_url")

    async def _list(self, **kwargs):
        return _page([_open_row(submission_id=77)])

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


async def _lost_response(monkeypatch, db, listed):
    _install(monkeypatch, db)
    calls = {"n": 0}

    async def _create(self, **kwargs):
        calls["n"] += 1
        raise RuntimeError("response_lost")

    async def _list(self, **kwargs):
        payload = listed() if callable(listed) else listed
        return payload

    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.create_submission", _create)
    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.list_submitters", _list)
    db["bond_cases"].docs.append(_case())
    with pytest.raises(RuntimeError, match="recovery_required"):
        await _build()
    assert calls["n"] == 1
    packet = db["paperwork_packets"].docs[0]
    assert packet["status"] == "recovery_required"
    assert packet["sign_url"] == ""
    assert db["bond_cases"].docs[0]["self_serve_saga_status"] == "open"
    assert db["indemnitors"].docs == []
    return packet


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("listed", "reason"),
    [
        (_page([_open_row(role="defendant")]), "role_mismatch"),
        (_page([_open_row(email="other@shamrockbailbonds.biz")]), "email_mismatch"),
        (_page([_open_row(phone="2395550199")]), "phone_mismatch"),
        (_page([_open_row(status="")]), "status_not_open"),
        (_page([_open_row(status="pending")]), "status_not_open"),
        (_page([_open_row(status="completed")]), "status_not_open"),
        (_page([_open_row(status="declined")]), "status_not_open"),
        (_page([_open_row(), _open_row(id=10)]), "lookup_not_unique"),
        (_page([_open_row()], count=2, nxt=100), "lookup_truncated"),
        ({"data": [_open_row()]}, "lookup_truncated"),
        (
            _page([_open_row(embed_src="https://evil.example/s/TEST-SLUG")]),
            "sign_url_rejected",
        ),
        (
            _page([_open_row(embed_src="http://sign.shamrockbailbonds.biz/s/TEST-SLUG")]),
            "sign_url_rejected",
        ),
        (
            _page([_open_row(embed_src="https://sign.shamrockbailbonds.biz/pay")]),
            "sign_url_rejected",
        ),
        (
            _page([_open_row(embed_src="https://pay.swipesimple.com/s/TEST-SLUG")]),
            "sign_url_rejected",
        ),
        (
            _page([_open_row(embed_src="https://sign.shamrockbailbonds.biz/s/TEST-SLUG?next=1")]),
            "sign_url_rejected",
        ),
        (
            _page([_open_row(embed_src="https://sign.shamrockbailbonds.biz/s/TEST-SLUG#section")]),
            "sign_url_rejected",
        ),
        (
            _page([_open_row(embed_src="https://user:pass@sign.shamrockbailbonds.biz/s/TEST-SLUG")]),
            "sign_url_rejected",
        ),
        (
            _page([_open_row(embed_src="https://sign.shamrockbailbonds.biz/s/OTHER-SLUG")]),
            "sign_url_rejected",
        ),
        (
            _page([_open_row(external_id=f"{_packet_id()}:defendant")]),
            "external_id_mismatch",
        ),
    ],
)
async def test_recovery_rejects_unbound_or_unsafe_submitter(monkeypatch, listed, reason):
    db = FakeDB()
    packet = await _lost_response(monkeypatch, db, listed)
    assert packet["recovery_reason"] == reason
    assert not any(row.get("event_type") == "self_serve_saga_recovered" for row in db["audit_events"].docs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "embed_src",
    [
        "https://evil.example/s/TEST-SLUG",
        "http://sign.shamrockbailbonds.biz/s/TEST-SLUG",
        "https://sign.shamrockbailbonds.biz/s/TEST-SLUG?next=1",
        "https://sign.shamrockbailbonds.biz/s/TEST-SLUG#section",
        "https://user:pass@sign.shamrockbailbonds.biz/s/TEST-SLUG",
        "https://sign.shamrockbailbonds.biz/s/OTHER-SLUG",
        "https://sign.shamrockbailbonds.biz/pay",
        "https://pay.swipesimple.com/s/TEST-SLUG",
    ],
)
async def test_initial_hostile_embed_src_is_not_a_signing_url(monkeypatch, embed_src):
    """The create response is an external effect. A bad embed is not signed,
    not compensated, and not submitted again.
    """
    db = FakeDB()
    _install(monkeypatch, db)
    db["bond_cases"].docs.append(_case())
    calls = {"n": 0}
    external_id = f"{_packet_id()}:indemnitor"

    async def _create(self, **kwargs):
        calls["n"] += 1
        return {
            "id": 55,
            "submitters": [{
                "id": 9,
                "submission_id": 55,
                "role": "indemnitor",
                "slug": "TEST-SLUG",
                "email": EMAIL,
                "phone": PHONE,
                "status": "awaiting",
                "external_id": external_id,
                "embed_src": embed_src,
            }],
        }

    async def _list(self, **kwargs):
        return _page([_open_row(embed_src=embed_src, submission_id=55)])

    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.create_submission", _create)
    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.list_submitters", _list)
    with pytest.raises(RuntimeError, match="recovery_required"):
        await _build()
    with pytest.raises(RuntimeError, match="recovery_required"):
        await _build()
    packet = db["paperwork_packets"].docs[0]
    assert calls["n"] == 1
    assert packet["status"] == "recovery_required"
    assert packet["status"] != "compensated"
    assert packet["docuseal_started"] is True
    assert packet.get("sign_url", "") == ""
    assert packet["recovery_reason"] == "sign_url_rejected"
    stored = packet["docuseal_submitters"][0]
    assert stored["sign_url"] == ""
    assert stored.get("embed_src", "") == ""
    assert embed_src not in str(packet)


@pytest.mark.asyncio
async def test_provider_lookup_error_does_not_create_another_submission(monkeypatch):
    db = FakeDB()
    _install(monkeypatch, db)
    db["bond_cases"].docs.append(_case())
    calls = {"n": 0}

    async def _create(self, **kwargs):
        calls["n"] += 1
        raise RuntimeError("timeout_after_accept")

    async def _list(self, **kwargs):
        raise RuntimeError("provider_down")

    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.create_submission", _create)
    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.list_submitters", _list)
    with pytest.raises(RuntimeError, match="recovery_required"):
        await _build()
    with pytest.raises(RuntimeError, match="recovery_required"):
        await _build()
    assert calls["n"] == 1
    assert db["paperwork_packets"].docs[0]["recovery_reason"] == "lookup_failed"
    assert db["paperwork_packets"].docs[0]["sign_url"] == ""


@pytest.mark.asyncio
async def test_signed_packet_is_not_rewritten_during_reconciliation(monkeypatch):
    db = FakeDB()
    _install(monkeypatch, db)
    db["bond_cases"].docs.append(_case())
    calls = {"n": 0}

    async def _create(self, **kwargs):
        calls["n"] += 1
        raise RuntimeError("response_lost")

    async def _list(self, **kwargs):
        db["paperwork_packets"].docs[0]["status"] = "signed"
        db["paperwork_packets"].docs[0]["docuseal_status"] = "completed"
        db["paperwork_packets"].docs[0]["sign_url"] = "https://sign.shamrockbailbonds.biz/s/TEST-SIGNED"
        return _page([_open_row()])

    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.create_submission", _create)
    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.list_submitters", _list)
    with pytest.raises(RuntimeError, match="recovery_required"):
        await _build()
    packet = db["paperwork_packets"].docs[0]
    assert calls["n"] == 1
    assert packet["status"] == "signed"
    assert packet["docuseal_status"] == "completed"
    assert packet["sign_url"].endswith("/s/TEST-SIGNED")
    assert not any(row.get("event_type") == "self_serve_saga_recovered" for row in db["audit_events"].docs)


@pytest.mark.asyncio
async def test_unpersisted_provider_ids_are_not_a_pre_dispatch_identity(monkeypatch):
    """DocuSeal assigns submission and submitter ids only after accept.

    This lost response never stored those ids. Recovery uses the external id,
    role, email, and phone saved before the call. The arbitrary ids on the
    lookup row are not compared to an expected provider id.
    """
    db = FakeDB()
    _install(monkeypatch, db)
    db["bond_cases"].docs.append(_case())
    seen = {}

    async def _create(self, **kwargs):
        raise RuntimeError("response_lost")

    async def _list(self, **kwargs):
        packet = db["paperwork_packets"].docs[0]
        seen["submission_before"] = packet.get("docuseal_submission_id")
        seen["submitters_before"] = list(packet.get("docuseal_submitters") or [])
        return _page([_open_row(id=3, submission_id=999)])

    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.create_submission", _create)
    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.list_submitters", _list)
    saved = await _build()
    assert seen["submission_before"] in (None, "")
    assert seen["submitters_before"] == []
    assert saved["recovered"] is True
    assert db["paperwork_packets"].docs[0]["docuseal_submission_id"] == 999


@pytest.mark.asyncio
async def test_persisted_provider_ids_reject_a_different_lookup_row(monkeypatch):
    """After accept, the stored submission id is the expected provider id.

    The create response has no slug, so the URL is not stored and the lookup
    runs. A different submission id from that lookup does not replace it.
    """
    db = FakeDB()
    _install(monkeypatch, db)
    db["bond_cases"].docs.append(_case())
    external_id = f"{_packet_id()}:indemnitor"

    async def _create(self, **kwargs):
        return {
            "id": 77,
            "submitters": [{
                "id": 9,
                "submission_id": 77,
                "role": "indemnitor",
                "email": EMAIL,
                "phone": PHONE,
                "status": "awaiting",
                "external_id": external_id,
            }],
        }

    async def _list(self, **kwargs):
        return _page([_open_row(id=9, submission_id=78)])

    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.create_submission", _create)
    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.list_submitters", _list)
    with pytest.raises(RuntimeError, match="recovery_required"):
        await _build()
    with pytest.raises(RuntimeError, match="recovery_required"):
        await _build()
    packet = db["paperwork_packets"].docs[0]
    assert packet["recovery_reason"] == "submission_id_mismatch"
    assert packet["docuseal_submission_id"] == 77
    assert packet["sign_url"] == ""
    assert packet["docuseal_submitters"][0]["id"] == 9
