"""Shannon voice intake: keep spelled names and skip matching on the hot path."""
from __future__ import annotations

import asyncio
import copy
from unittest.mock import AsyncMock, MagicMock, patch

from dashboard.routers.intake import _extract_defendant, _extract_indemnitor, _normalize_source


def test_extract_indemnitor_uses_full_indemnitor_name():
    out = _extract_indemnitor({"indemnitorName": "Brendan O'Neal", "indemnitorPhone": "2397849365"})
    assert out["firstName"] == "Brendan"
    assert out["lastName"] == "O'Neal"
    assert out["phone"] == "2397849365"


def test_extract_indemnitor_does_not_invent_oneill():
    out = _extract_indemnitor({"caller_name": "Brendan O'Neal"})
    assert out["lastName"] == "O'Neal"
    assert "Neill" not in out["lastName"]


def test_normalize_shannon_source():
    assert _normalize_source("elevenlabs_voice") == "elevenlabs_voice"
    assert _normalize_source("shannon") == "shannon"
    assert _normalize_source("shannon_voice") == "shannon_voice"
    assert _normalize_source("telegram_miniapp") == "telegram_miniapp"
    assert _normalize_source("telegram") == "telegram"


def test_shannon_intake_submit_skips_matching(monkeypatch):
    from dashboard.routers import intake as intake_mod

    col = MagicMock()
    col.update_one = AsyncMock()
    monkeypatch.setattr(intake_mod, "get_collection", lambda name: col)

    class Req:
        headers = {}

        async def json(self):
            return {
                "source": "elevenlabs_voice",
                "intakeId": "SH-2397849365-JANE-DOE",
                "defendantName": "Jane Doe",
                "indemnitorName": "Brendan O'Neal",
                "skip_match": True,
            }

    with patch("dashboard.services.matching_engine.MatchingEngine") as engine_cls, \
         patch("dashboard.services.intake_fanout.schedule_after_save") as sched:
        result = asyncio.run(intake_mod.intake_submit(Req()))
    assert result["success"] is True
    assert result["intake_id"] == "SH-2397849365-JANE-DOE"
    assert result["indemnitor_name"] == "Brendan O'Neal"
    assert result["match"] is None
    assert result["payment_link"].startswith("https://")
    engine_cls.assert_not_called()
    sched.assert_called_once()
    saved = col.update_one.await_args.args[1]["$set"]
    assert saved["indemnitor_name"] == "Brendan O'Neal"


def test_repeat_intake_submit_does_not_regress_lifecycle_fields(monkeypatch):
    from dashboard.routers import intake as intake_mod

    col = MagicMock()
    col.update_one = AsyncMock()
    monkeypatch.setattr(intake_mod, "get_collection", lambda name: col)

    class Req:
        headers = {}

        async def json(self):
            return {
                "source": "shannon_voice",
                "intakeId": "SH-2395550101-JANE-DOE",
                "defendantName": "Jane Doe",
                "indemnitorName": "Brendan O'Neal",
                "indemnitorPhone": "2395550101",
            }

    with patch("dashboard.services.intake_fanout.schedule_after_save"):
        result = asyncio.run(intake_mod.intake_submit(Req()))

    assert result["success"] is True
    update = col.update_one.await_args.args[1]
    saved = update["$set"]
    inserted = update["$setOnInsert"]
    assert saved["indemnitor_name"] == "Brendan O'Neal"
    assert saved["indemnitor_phone"] == "2395550101"
    preserved = (
        "status",
        "created_at",
        "matched_booking_number",
        "matched_county",
        "matched_defendant_id",
        "match_confidence",
        "match_strategy",
        "match_timestamp",
        "surety_id",
        "surety_unrecognized",
        "paperwork_packet_id",
        "paperwork_status",
    )
    for key in preserved:
        assert key not in saved, key
        assert key in inserted, key
    assert inserted["status"] == "pending"
    assert inserted["surety_id"] is None
    assert inserted["paperwork_packet_id"] is None
    overlap = set(saved) & set(inserted)
    assert overlap == set()


def test_normalize_intake_preserves_lifecycle_fields(monkeypatch):
    from dashboard.routers import intake as intake_mod

    col = MagicMock()
    col.update_one = AsyncMock()
    monkeypatch.setattr(intake_mod, "get_collection", lambda name: col)
    asyncio.run(intake_mod._normalize_intake(
        {"intakeId": "WX-1", "indemnitorName": "Amy Roe"},
        source="wix_webhook",
    ))
    update = col.update_one.await_args.args[1]
    assert update["$set"]["indemnitor_name"] == "Amy Roe"
    assert "status" not in update["$set"]
    assert "created_at" not in update["$set"]
    assert update["$setOnInsert"]["status"] == "pending"
    assert "paperwork_packet_id" in update["$setOnInsert"]
    assert "surety_id" in update["$setOnInsert"]


def _apply_path(doc, path, value):
    parts = path.split(".")
    node = doc
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = copy.deepcopy(value)


def _apply_intake_update(existing, update, *, inserted):
    """Apply one intake upsert the way Mongo does, including dotted paths."""
    doc = copy.deepcopy(existing) if existing is not None else {}
    if inserted:
        for path, value in (update.get("$setOnInsert") or {}).items():
            _apply_path(doc, path, value)
    for path, value in (update.get("$set") or {}).items():
        _apply_path(doc, path, value)
    return doc


def _submit(monkeypatch, payloads):
    from dashboard.routers import intake as intake_mod

    col = MagicMock()
    col.update_one = AsyncMock()
    monkeypatch.setattr(intake_mod, "get_collection", lambda name: col)

    class Req:
        def __init__(self, body):
            self.headers = {}
            self._body = body

        async def json(self):
            return self._body

    with patch("dashboard.services.intake_fanout.schedule_after_save"):
        for body in payloads:
            asyncio.run(intake_mod.intake_submit(Req(body)))
    return [call.args[1] for call in col.update_one.await_args_list]


_FULL_PAPERWORK = {
    "source": "shannon_voice",
    "intakeId": "SH-2395550101-JANE-DOE",
    "defendantName": "Jane Doe",
    "defendantDOB": "1990-03-04",
    "DefDL": "E9988776",
    "DefCharges": "Example charge",
    "bondAmount": "5000",
    "indemnitorName": "Pat Example",
    "indemnitorPhone": "2395550101",
    "indemnitorEmail": "pat@example.com",
    "indemnitorAddress": "10 Palm St",
    "indemnitorDOB": "1980-01-02",
    "indemnitorDL": "D1234567",
    "indemnitorEmployerName": "Harbor Cafe",
    "reference1Name": "Sam Roe",
    "reference1Phone": "2395550199",
    "reference1Relation": "sibling",
}

_THIN_SUBMIT = {
    "source": "shannon_voice",
    "intakeId": "SH-2395550101-JANE-DOE",
    "defendantName": "Jane Doe",
    "indemnitorName": "Pat Example",
    "indemnitorPhone": "2395550101",
}


def test_full_save_then_thin_submit_keeps_stored_fields(monkeypatch):
    updates = _submit(monkeypatch, [_FULL_PAPERWORK, _THIN_SUBMIT])
    stored = _apply_intake_update(None, updates[0], inserted=True)
    stored = _apply_intake_update(stored, updates[1], inserted=False)
    indemnitor = stored["indemnitor"]
    defendant = stored["defendant"]
    assert indemnitor["email"] == "pat@example.com"
    assert indemnitor["address"] == "10 Palm St"
    assert indemnitor["dob"] == "1980-01-02"
    assert indemnitor["dl"] == "D1234567"
    assert indemnitor["employer"] == "Harbor Cafe"
    assert indemnitor["ref1Name"] == "Sam Roe"
    assert indemnitor["ref1Phone"] == "2395550199"
    assert indemnitor["ref1Relation"] == "sibling"
    assert defendant["charges"] == "Example charge"
    assert defendant["bondAmount"] == "5000"
    assert defendant["dob"] == "1990-03-04"
    assert defendant["dl"] == "E9988776"
    assert stored["indemnitor_email"] == "pat@example.com"
    thin = updates[1]
    assert "indemnitor" not in thin["$set"]
    assert "defendant" not in thin["$set"]
    for path in (
        "indemnitor.email",
        "indemnitor.address",
        "indemnitor.dob",
        "indemnitor.dl",
        "indemnitor.employer",
        "indemnitor.ref1Name",
        "defendant.charges",
        "defendant.bondAmount",
        "indemnitor_email",
    ):
        assert path not in thin["$set"], path


def test_new_nonempty_value_still_updates(monkeypatch):
    revised = dict(_THIN_SUBMIT)
    revised["indemnitorEmail"] = "next@example.com"
    revised["bondAmount"] = "7500"
    updates = _submit(monkeypatch, [_FULL_PAPERWORK, revised])
    stored = _apply_intake_update(None, updates[0], inserted=True)
    stored = _apply_intake_update(stored, updates[1], inserted=False)
    assert stored["indemnitor"]["email"] == "next@example.com"
    assert stored["indemnitor_email"] == "next@example.com"
    assert stored["defendant"]["bondAmount"] == "7500"
    assert stored["indemnitor"]["address"] == "10 Palm St"
    assert stored["indemnitor"]["dob"] == "1980-01-02"
    assert stored["indemnitor"]["dl"] == "D1234567"
    assert stored["indemnitor"]["employer"] == "Harbor Cafe"
    assert stored["indemnitor"]["ref1Name"] == "Sam Roe"
    assert stored["defendant"]["charges"] == "Example charge"
    assert "indemnitor.email" in updates[1]["$set"]
    assert "indemnitor.address" not in updates[1]["$set"]


def test_first_insert_still_stores_the_full_document(monkeypatch):
    updates = _submit(monkeypatch, [_FULL_PAPERWORK])
    update = updates[0]
    assert set(update["$set"]) & set(update["$setOnInsert"]) == set()
    assert "indemnitor" not in update["$set"]
    assert "defendant" not in update["$set"]
    assert "indemnitor" not in update["$setOnInsert"]
    assert "defendant" not in update["$setOnInsert"]
    stored = _apply_intake_update(None, update, inserted=True)
    assert stored["indemnitor"] == _extract_indemnitor(_FULL_PAPERWORK, apply_defaults=False)
    assert stored["defendant"] == _extract_defendant(_FULL_PAPERWORK, apply_defaults=False)
    assert stored["indemnitor_email"] == "pat@example.com"
    assert stored["indemnitor_phone"] == "2395550101"
    assert stored["defendant_name"] == "Jane Doe"
    assert stored["status"] == "pending"
    assert stored["surety_id"] is None
    assert stored["paperwork_packet_id"] is None
    assert stored["indemnitor"]["ssn"] == ""
    assert stored["defendant"]["charge_details"] == []
    assert "indemnitor.ssn" in update["$setOnInsert"]
    assert "defendant.charge_details" in update["$setOnInsert"]
    assert "indemnitor.email" in update["$set"]
    assert "defendant.charges" in update["$set"]
    assert "defendant.bondAmount" in update["$set"]
