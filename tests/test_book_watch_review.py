"""BailSafe P0 slice A1 — Book Watch review queue, session actor, forfeited watch."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId

ROOT = Path(__file__).resolve().parents[1]


class _AsyncChain:
    def __init__(self, items):
        self._items = list(items)
        self._i = 0

    def sort(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._i]
        self._i += 1
        return item


class _ToList:
    def __init__(self, items):
        self._items = list(items)

    async def to_list(self, length=20):
        return list(self._items)


def _session(email="kayla@shamrockbailbonds.biz"):
    return {"auth": True, "email": email, "agent_name": "Kayla Lukesic"}


def test_book_watch_ui_contract():
    source = (ROOT / "dashboard" / "sl-rearrest.js").read_text()
    page = (ROOT / "dashboard" / "index.html").read_text()
    css = (ROOT / "dashboard" / "styles.css").read_text()

    assert "Book Watch" in page
    assert "sl-rearrest.js?v=6" in page
    assert "/api/rearrest/pending?limit=25&include=pending_review,unconfirmed_triage" in source
    assert "needs_identity_check" in source
    assert "Needs identity check" in source
    assert "New arrest" in source
    assert "Bond on file" in source
    assert "JSON.stringify({ action, notes: notes || '' })" in source
    assert "/action" in source
    for action in ("revoke", "second_bond", "false_positive", "contacted", "dismiss", "notify_indemnitor"):
        assert action in source
    assert "reviewed_by:" not in source
    assert "contacted_by:" not in source
    assert '"actor"' not in source
    assert "ra-conf" in css
    assert "ra-evidence" in css


def test_scraper_checker_does_not_auto_text_and_uses_identity_lane():
    source = (ROOT / "writers" / "rearrest_checker.py").read_text()
    assert "send_human_like" not in source
    assert '"unconfirmed_triage"' in source or "'unconfirmed_triage'" in source
    assert "scraper_path_unscored" in source
    assert "indemnitor_auto_text" in source
    assert "blocked" in source


@pytest.mark.asyncio
async def test_pending_splits_identity_lane_from_review_queue():
    from dashboard.routers.rearrest_notifier import api_rearrest_pending

    review_id = ObjectId()
    low_id = ObjectId()
    identity_id = ObjectId()
    docs = {
        "pending_review": [
            {
                "_id": review_id,
                "status": "pending_review",
                "confidence": "high",
                "defendant_name": "REVIEW DEFENDANT",
                "county": "Lee",
                "booking_number": "NEW-1",
                "arrest_dob": "1990-01-15",
                "prior_defendant_name": "Review Defendant",
                "prior_booking_number": "OLD-1",
                "bond_dob": "01/15/1990",
                "prior_county": "Lee",
                "prior_bond_status": "forfeited",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "_id": low_id,
                "status": "pending_review",
                "confidence": "low",
                "defendant_name": "LOW DEFENDANT",
                "booking_number": "NEW-LOW",
                "created_at": datetime.now(timezone.utc),
            },
        ],
        "unconfirmed_triage": [
            {
                "_id": identity_id,
                "status": "unconfirmed_triage",
                "confidence": "low",
                "confidence_reason": "scraper_path_unscored",
                "defendant_name": "IDENTITY DEFENDANT",
                "booking_number": "NEW-2",
                "created_at": datetime.now(timezone.utc),
            }
        ],
    }

    notifications = MagicMock()
    notifications.find = MagicMock(side_effect=lambda query, *a, **k: _AsyncChain(docs[query["status"]]))

    with patch("dashboard.routers.rearrest_notifier.get_collection", return_value=notifications):
        res = await api_rearrest_pending(limit=25, include="pending_review,unconfirmed_triage")

    assert res["counts"]["pending_review"] == 1
    assert res["counts"]["needs_identity_check"] == 2
    assert res["lanes"]["pending_review"][0]["_id"] == str(review_id)
    assert res["lanes"]["pending_review"][0]["evidence"]["bond"]["status"] == "forfeited"
    assert res["lanes"]["pending_review"][0]["evidence"]["arrest"]["dob"] == "1990-01-15"
    identity_ids = {row["_id"] for row in res["lanes"]["needs_identity_check"]}
    assert identity_ids == {str(low_id), str(identity_id)}


@pytest.mark.asyncio
async def test_triage_actor_comes_from_session_and_forfeited_skips_illegal_transition():
    from dashboard.routers.rearrest_notifier import api_rearrest_action

    notif_id = str(ObjectId())
    doc = {
        "_id": ObjectId(notif_id),
        "booking_number": "BK-NEW",
        "prior_booking_number": "BK-OLD",
        "defendant_name": "Forfeited Defendant",
        "county": "Lee",
        "confidence": "confirmed",
        "status": "pending_review",
    }
    notifications = MagicMock()
    notifications.find_one = AsyncMock(return_value=doc)
    notifications.update_one = AsyncMock()
    bonds = MagicMock()
    bonds.find_one = AsyncMock(return_value={"booking_number": "BK-OLD", "status": "forfeited"})
    bonds.update_one = AsyncMock()
    audit = MagicMock()
    audit.insert_one = AsyncMock()

    def get_col(name):
        return {"rearrest_notifications": notifications, "active_bonds": bonds, "audit_events": audit}[name]

    request = MagicMock()
    request.json = AsyncMock(return_value={"action": "revoke", "actor": "Brendan", "reviewed_by": "Brendan"})

    with patch("dashboard.routers.rearrest_notifier.get_collection", side_effect=get_col), patch(
        "dashboard.routers.rearrest_notifier.get_session_from_request", return_value=None
    ):
        denied = await api_rearrest_action(request, notif_id)
    assert denied.status_code == 401

    with patch("dashboard.routers.rearrest_notifier.get_collection", side_effect=get_col), patch(
        "dashboard.routers.rearrest_notifier.get_session_from_request", return_value=_session()
    ), patch(
        "dashboard.services.state_machine.BondStateMachine.transition_bond", new_callable=AsyncMock
    ) as transition:
        res = await api_rearrest_action(request, notif_id)

    assert res["success"] is True
    assert res["actor"] == "session:Kayla Lukesic"
    assert res["bond_status_unchanged"] is True
    assert "forfeited" in res["transition_note"]
    transition.assert_not_awaited()
    flag_update = bonds.update_one.await_args.args[1]["$set"]
    assert flag_update["bond_revocation_flag"] is True
    assert "status" not in flag_update
    stored = notifications.update_one.await_args.args[1]["$set"]
    assert stored["action_by"] == "session:Kayla Lukesic"
    assert "Brendan" not in stored["action_by"]
    assert audit.insert_one.await_args.args[0]["actor"] == "session:Kayla Lukesic"


@pytest.mark.asyncio
async def test_false_positive_writes_audit_and_ignores_body_actor():
    from dashboard.routers.rearrest_notifier import api_rearrest_action

    notif_id = str(ObjectId())
    notifications = MagicMock()
    notifications.find_one = AsyncMock(return_value={
        "_id": ObjectId(notif_id),
        "booking_number": "BK-NEW",
        "prior_booking_number": "BK-OLD",
        "confidence": "probable",
        "status": "pending_review",
    })
    notifications.update_one = AsyncMock()
    bonds = MagicMock()
    bonds.update_one = AsyncMock()
    audit = MagicMock()
    audit.insert_one = AsyncMock()

    def get_col(name):
        return {"rearrest_notifications": notifications, "active_bonds": bonds, "audit_events": audit}[name]

    request = MagicMock()
    request.json = AsyncMock(return_value={"action": "false_positive", "actor": "Not Staff", "notes": "different person"})

    with patch("dashboard.routers.rearrest_notifier.get_collection", side_effect=get_col), patch(
        "dashboard.routers.rearrest_notifier.get_session_from_request",
        return_value=_session("staff@shamrockbailbonds.biz"),
    ):
        res = await api_rearrest_action(request, notif_id)

    assert res["success"] is True
    assert res["status"] == "false_positive"
    assert res["actor"] == "session:Kayla Lukesic"
    event = audit.insert_one.await_args.args[0]
    assert event["event_type"] == "rearrest_triage_false_positive"
    assert event["actor"].startswith("session:")
    assert event["notes"] == "different person"


@pytest.mark.asyncio
async def test_check_queues_without_indemnitor_text():
    from dashboard.routers.rearrest_notifier import check_and_notify_rearrest

    bonds = MagicMock()
    bonds.find = MagicMock(return_value=_ToList([
        {
            "defendant_name": "JOHN SMITH",
            "county": "Collier",
            "booking_number": "OLD-SMITH",
            "status": "active",
            "indemnitor_phone": "+12395550178",
            "indemnitor_name": "Pat Smith",
        }
    ]))
    notifications = MagicMock()
    notifications.find_one = AsyncMock(return_value=None)
    notifications.insert_one = AsyncMock()

    def get_col(name):
        return {"active_bonds": bonds, "rearrest_notifications": notifications}[name]

    with patch("dashboard.routers.rearrest_notifier.get_collection", side_effect=get_col), patch(
        "dashboard.routers.rearrest_notifier.BlueBubblesClient"
    ) as bb:
        result = await check_and_notify_rearrest(
            defendant_name="JOHN SMITH",
            county="Lee",
            booking_number="NEW-SMITH",
        )

    assert result["notifications_sent"] == 0
    assert result["notifications_queued"] == 1
    assert result["auto_text"] == "blocked_pending_staff_approval"
    bb.assert_not_called()
    queued = notifications.insert_one.await_args.args[0]
    assert queued["status"] == "unconfirmed_triage"
    assert queued["confidence"] == "low"
    assert queued["indemnitor_auto_text"] == "blocked"


@pytest.mark.asyncio
async def test_notify_indemnitor_blocked_for_low_confidence():
    from dashboard.routers.rearrest_notifier import api_rearrest_action

    notif_id = str(ObjectId())
    notifications = MagicMock()
    notifications.find_one = AsyncMock(return_value={
        "_id": ObjectId(notif_id),
        "confidence": "low",
        "indemnitor_phone": "+12395550178",
        "defendant_name": "JOHN SMITH",
        "county": "Lee",
        "status": "unconfirmed_triage",
    })
    notifications.update_one = AsyncMock()
    audit = MagicMock()
    audit.insert_one = AsyncMock()

    def get_col(name):
        return {
            "rearrest_notifications": notifications,
            "active_bonds": MagicMock(),
            "audit_events": audit,
        }[name]

    request = MagicMock()
    request.json = AsyncMock(return_value={"action": "notify_indemnitor", "staff_approved": True, "actor": "Brendan"})

    with patch("dashboard.routers.rearrest_notifier.get_collection", side_effect=get_col), patch(
        "dashboard.routers.rearrest_notifier.get_session_from_request", return_value=_session()
    ), patch("dashboard.routers.rearrest_notifier.BlueBubblesClient") as bb:
        res = await api_rearrest_action(request, notif_id)

    assert res.status_code == 409
    bb.assert_not_called()
    audit.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan_matches_forfeited_bond_into_review_queue():
    from dashboard.routers.rearrest_detector import scan_for_rearrests

    now = datetime.now(timezone.utc)
    bond = {
        "_id": "bond-forf",
        "status": "forfeited",
        "defendant_name": "FORFEITED WATCH DEFENDANT",
        "dob": "01/15/1990",
        "booking_number": "OLD-FORF",
        "bond_amount": 8000,
        "county": "Lee",
        "case_number": "26-CF-9",
        "poa_number": "OSI-FORF",
    }
    arrest = {
        "full_name": "FORFEITED WATCH DEFENDANT",
        "dob": "01/15/1990",
        "booking_number": "NEW-FORF",
        "county": "Lee",
        "charges": "Battery",
        "bond_amount": 1500,
        "scraped_at": now.isoformat(),
        "custody_status": "In Custody",
    }
    bonds = MagicMock()
    bonds.find = MagicMock(return_value=_AsyncChain([bond]))
    bonds.update_one = AsyncMock()
    arrests = MagicMock()
    arrests.find = MagicMock(return_value=_AsyncChain([arrest]))
    rearrest = MagicMock()
    rearrest.find_one = AsyncMock(return_value=None)
    rearrest.insert_one = AsyncMock()

    def get_col(name):
        return {"arrests": arrests, "active_bonds": bonds, "rearrest_notifications": rearrest}[name]

    with patch("dashboard.routers.rearrest_detector.get_collection", side_effect=get_col), patch(
        "dashboard.routers.events.publish_event", new_callable=AsyncMock
    ), patch(
        "dashboard.routers.notifications.create_notification", new_callable=AsyncMock
    ), patch(
        "dashboard.routers.rearrest_detector._post_rearrest_slack", new_callable=AsyncMock
    ):
        result = await scan_for_rearrests(hours=24)

    assert result["detected"] == 1
    assert "forfeited" in bonds.find.call_args.args[0]["status"]["$in"]
    inserted = rearrest.insert_one.await_args.args[0]
    assert inserted["prior_bond_status"] == "forfeited"
    assert inserted["confidence"] == "confirmed"
    assert inserted["status"] == "pending_review"
    assert inserted["arrest_dob"] == "01/15/1990"
    assert inserted["bond_dob"] == "01/15/1990"
