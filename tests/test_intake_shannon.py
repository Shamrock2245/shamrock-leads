"""Shannon voice intake: keep spelled names and skip matching on the hot path."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from dashboard.routers.intake import _extract_indemnitor, _normalize_source


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
