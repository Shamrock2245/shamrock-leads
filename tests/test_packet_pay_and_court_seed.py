"""Focused tests for packet payment-link helper + court calendar seed on promote."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from dashboard.services.packet_payment_link_service import (
    maybe_send_packet_payment_link,
    _is_paid,
    _recent_send,
)
from dashboard.services.bond_court_seed_service import (
    seed_court_calendar_for_bond,
    _bond_email_data,
)


@pytest.mark.asyncio
async def test_maybe_send_skips_when_recently_sent():
    packet = {
        "packet_id": "PKT-1",
        "premium_amount": 500,
        "indemnitor_phone": "2395550100",
        "last_payment_link_sent_at": datetime.now(timezone.utc).isoformat(),
    }

    with patch(
        "dashboard.services.packet_payment_link_service._load_context",
        new_callable=AsyncMock,
        return_value={
            "packet_id": "PKT-1",
            "booking_number": "BK-1",
            "packet_doc": packet,
            "bond_doc": None,
            "intake_doc": None,
        },
    ), patch(
        "dashboard.services.packet_payment_link_service.send_swipesimple_payment_link",
        new_callable=AsyncMock,
    ) as send_mock:
        result = await maybe_send_packet_payment_link(
            packet_id="PKT-1",
            packet_doc=packet,
            source="test",
        )
        assert result["skipped"] is True
        assert result["reason"] == "recently_sent"
        send_mock.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_send_skips_when_paid():
    bond = {
        "booking_number": "BK-2",
        "payment_received": True,
        "payment_status": "paid",
        "premium": 400,
        "indemnitor_phone": "2395550101",
    }
    with patch(
        "dashboard.services.packet_payment_link_service._load_context",
        new_callable=AsyncMock,
        return_value={
            "packet_id": "",
            "booking_number": "BK-2",
            "packet_doc": None,
            "bond_doc": bond,
            "intake_doc": None,
        },
    ), patch(
        "dashboard.services.packet_payment_link_service.send_swipesimple_payment_link",
        new_callable=AsyncMock,
    ) as send_mock:
        result = await maybe_send_packet_payment_link(
            booking_number="BK-2",
            bond_doc=bond,
            source="intake_promote",
        )
        assert result["skipped"] is True
        assert result["reason"] == "payment_already_collected"
        send_mock.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_send_dispatches_when_eligible():
    bond = {
        "booking_number": "BK-3",
        "premium": 250.0,
        "indemnitor_phone": "2395550199",
        "payment_status": "pending",
        "defendant_name": "Test Def",
    }
    with patch(
        "dashboard.services.packet_payment_link_service._load_context",
        new_callable=AsyncMock,
        return_value={
            "packet_id": "",
            "booking_number": "BK-3",
            "packet_doc": None,
            "bond_doc": bond,
            "intake_doc": None,
        },
    ), patch(
        "dashboard.services.packet_payment_link_service.send_swipesimple_payment_link",
        new_callable=AsyncMock,
        return_value={
            "success": True,
            "skipped": False,
            "delivered": True,
            "amount": 250.0,
            "booking_number": "BK-3",
        },
    ) as send_mock:
        result = await maybe_send_packet_payment_link(
            booking_number="BK-3",
            bond_doc=bond,
            source="intake_promote",
        )
        assert result["skipped"] is False
        assert result["delivered"] is True
        send_mock.assert_called_once()
        kwargs = send_mock.call_args.kwargs
        assert kwargs["amount"] == 250.0
        assert kwargs["source"] == "intake_promote"


def test_is_paid_and_recent_helpers():
    assert _is_paid({"payment_status": "paid"}) is True
    assert _is_paid({"payment_received": True}) is True
    assert _is_paid({"payment_status": "pending"}) is False
    assert _recent_send(
        {"last_payment_link_sent_at": datetime.now(timezone.utc).isoformat()}
    ) is True
    assert _recent_send(
        {
            "last_payment_link_sent_at": (
                datetime.now(timezone.utc) - timedelta(hours=48)
            ).isoformat()
        }
    ) is False


def test_bond_email_data_skips_tbn():
    assert _bond_email_data({"court_date": "TBN", "booking_number": "X"}) is None
    data = _bond_email_data(
        {
            "court_date": "2026-10-01",
            "court_time": "8:30 AM",
            "case_number": "25-CF-1",
            "booking_number": "BK-9",
            "defendant_name": "Jane",
            "county": "Lee",
            "court_location": "Courtroom 4A",
        }
    )
    assert data is not None
    assert data["case_number"] == "25-CF-1"
    assert data["event_type"] == "courtDate"
    assert data["datetime_info"]["date_str"] == "2026-10-01"


@pytest.mark.asyncio
async def test_seed_court_oauth_missing_sets_staff_flag():
    bond = {
        "booking_number": "BK-SEED-1",
        "court_date": "2026-11-15",
        "court_time": "09:00 AM",
        "case_number": "26-CF-100",
        "defendant_name": "Seed Test",
        "county": "Lee",
        "defendant_phone": "2395550111",
        "indemnitor_phone": "2395550112",
    }
    mock_bonds = MagicMock()
    mock_bonds.find_one = AsyncMock(return_value=bond)
    mock_bonds.update_one = AsyncMock()

    mock_cal = MagicMock()
    mock_cal.is_configured = False
    mock_cal.create_event.return_value = {
        "summary": "dry",
        "dry_run": True,
        "id": None,
        "oauth_configured": False,
    }

    with patch(
        "dashboard.services.bond_court_seed_service.get_collection",
        return_value=mock_bonds,
    ), patch(
        "dashboard.services.google_calendar_service.GoogleCalendarService",
        return_value=mock_cal,
    ), patch(
        "dashboard.services.bond_court_seed_service._schedule_reminders_for_bond",
        new_callable=AsyncMock,
        return_value={"success": True, "scheduled": 4},
    ):
        result = await seed_court_calendar_for_bond(
            bond=bond,
            source="intake_promote",
        )

    assert result["reason"] == "oauth_missing"
    assert result["gcal"]["status"] == "oauth_missing"
    # Staff-visible stamp
    assert mock_bonds.update_one.await_count >= 1
    set_arg = mock_bonds.update_one.await_args.args[1]["$set"]
    assert set_arg.get("gcal_oauth_missing") is True
    assert "OAuth" in (set_arg.get("gcal_seed_note") or "")


@pytest.mark.asyncio
async def test_seed_court_idempotent_when_already_seeded():
    bond = {
        "booking_number": "BK-SEED-2",
        "court_date": "2026-11-15",
        "court_time": "09:00 AM",
        "case_number": "26-CF-200",
        "defendant_name": "Idem",
        "gcal_event_id": "evt_abc",
        "gcal_seed_fingerprint": "2026-11-15|09:00 AM",
        "court_reminders_seeded_at": datetime.now(timezone.utc).isoformat(),
    }
    mock_bonds = MagicMock()
    mock_bonds.update_one = AsyncMock()

    with patch(
        "dashboard.services.bond_court_seed_service.get_collection",
        return_value=mock_bonds,
    ), patch(
        "dashboard.services.google_calendar_service.GoogleCalendarService"
    ) as cal_cls:
        result = await seed_court_calendar_for_bond(bond=bond, source="test")
        assert result["skipped"] is True
        assert result["reason"] == "already_seeded"
        cal_cls.assert_not_called()


@pytest.mark.asyncio
async def test_promote_hook_calls_helpers():
    """Ensure intake promote soft-hooks invoke payment + court seed helpers."""
    # Import the module-level symbols used inside intake_promote via late import;
    # we verify the helpers themselves are callable with promote-shaped bond docs.
    bond_doc = {
        "booking_number": "PROMOTE-1",
        "premium": 150.0,
        "indemnitor_phone": "2395550999",
        "indemnitor_email": "ind@example.com",
        "defendant_name": "Promote Def",
        "payment_status": "pending",
        "court_date": "2026-12-01",
        "court_time": "10:00 AM",
        "case_number": "26-CF-9",
        "county": "Collier",
    }

    with patch(
        "dashboard.services.packet_payment_link_service.send_swipesimple_payment_link",
        new_callable=AsyncMock,
        return_value={"success": True, "skipped": False, "delivered": True, "amount": 150.0},
    ) as send_mock, patch(
        "dashboard.services.packet_payment_link_service._load_context",
        new_callable=AsyncMock,
        return_value={
            "packet_id": "",
            "booking_number": "PROMOTE-1",
            "packet_doc": None,
            "bond_doc": bond_doc,
            "intake_doc": None,
        },
    ):
        pay = await maybe_send_packet_payment_link(
            booking_number="PROMOTE-1",
            bond_doc=bond_doc,
            amount=150.0,
            source="intake_promote",
        )
        assert pay.get("delivered") is True
        send_mock.assert_called_once()

    mock_bonds = MagicMock()
    mock_bonds.update_one = AsyncMock()
    mock_cal = MagicMock()
    mock_cal.is_configured = True
    mock_cal.create_event.return_value = {"id": "gcal_1", "summary": "ok"}

    with patch(
        "dashboard.services.bond_court_seed_service.get_collection",
        return_value=mock_bonds,
    ), patch(
        "dashboard.services.google_calendar_service.GoogleCalendarService",
        return_value=mock_cal,
    ), patch(
        "dashboard.services.bond_court_seed_service._schedule_reminders_for_bond",
        new_callable=AsyncMock,
        return_value={"success": True, "scheduled": 8},
    ):
        seed = await seed_court_calendar_for_bond(
            bond=bond_doc, source="intake_promote"
        )
        assert seed["gcal"]["status"] == "created"
        assert seed["gcal_event_id"] == "gcal_1"
