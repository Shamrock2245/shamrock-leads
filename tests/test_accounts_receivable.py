"""Accounts receivable balance, quiet hours, and opt-out gates."""

from datetime import date, datetime, timezone
import inspect

from dashboard.services.ar_llm import template_draft
from dashboard.services.ar_math import (
    build_ar_row,
    choose_tone,
    evaluate_reminder_send,
    within_contact_window,
)
from dashboard.services import ar_service


def _bond(**extra):
    base = {
        "booking_number": "BK-100",
        "defendant_name": "Alex Defendant",
        "indemnitor_name": "Jordan Indemnitor",
        "indemnitor_phone": "2395550142",
        "defendant_phone": "2395550199",
        "case_number": "26-CF-100",
        "poa_number": "OSI-200",
        "premium_cents": 50000,
        "down_payment_cents": 20000,
        "down_payment_entered_at": "2026-08-01T15:00:00+00:00",
        "next_payment_due": "2026-09-01",
    }
    base.update(extra)
    return base


def test_balance_is_premium_minus_down_payment_minus_later_payments():
    ledger = [{
        "booking_number": "BK-100",
        "type": "payment",
        "entry_kind": "payment",
        "amount": -15000,
        "amount_is_cents": True,
        "method": "check",
        "reference": "1044",
        "entered_by": "Brendan O'Neal",
        "timestamp": "2026-09-02T15:00:00+00:00",
    }]
    row = build_ar_row(_bond(), ledger_entries=ledger, today=date(2026, 9, 10))
    # 500.00 - 200.00 - 150.00 = 150.00
    assert row["balance_due_cents"] == 15000
    assert row["balance_due_dollars"] == "150.00"
    assert row["down_payment_cents"] == 20000
    assert row["later_payments_cents"] == 15000
    assert "balance_due" not in row or row["balance_due_cents"] == 15000


def test_stored_balance_field_is_ignored():
    bond = _bond(balance_due=1, balance_remaining=1, balance_due_cents=1)
    row = build_ar_row(bond, today=date(2026, 9, 10))
    assert row["balance_due_cents"] == 30000


def test_down_payment_ledger_row_is_not_counted_twice():
    ledger = [{
        "booking_number": "BK-100",
        "type": "down_payment",
        "entry_kind": "down_payment",
        "amount": -20000,
        "amount_is_cents": True,
        "stripe_swipe_ref": "write-bond-down:BK-100",
        "timestamp": "2026-08-01T15:00:00+00:00",
    }]
    row = build_ar_row(_bond(), ledger_entries=ledger, today=date(2026, 8, 2))
    assert row["later_payments_cents"] == 0
    assert row["balance_due_cents"] == 30000
    assert str(row["down_payment_entered_at"]).startswith("2026-08-01")


def test_swipesimple_transaction_links_once_by_booking_number():
    ledger = [{
        "booking_number": "BK-100",
        "type": "payment",
        "amount": -10000,
        "amount_is_cents": True,
        "stripe_swipe_ref": "SS-DEDUP-1",
        "actor": "SwipeSimple Import",
        "source": "swipesimple",
        "timestamp": "2026-09-03T15:00:00+00:00",
    }]
    transactions = [{
        "booking_number": "BK-100",
        "amount": 100.00,
        "type": "premium",
        "source": "swipesimple",
        "reference_id": "SS-DEDUP-1",
        "transaction_id": "SS-DEDUP-1",
        "status": "completed",
        "timestamp": "2026-09-03T15:00:00+00:00",
    }]
    row = build_ar_row(
        _bond(),
        ledger_entries=ledger,
        transactions=transactions,
        today=date(2026, 9, 10),
    )
    assert row["later_payments_cents"] == 10000
    assert row["balance_due_cents"] == 20000
    assert any(item["swipesimple"] for item in row["payments"])


def test_unattributed_swipesimple_row_is_not_guessed_onto_a_bond():
    transactions = [{
        "booking_number": "",
        "amount": 100.00,
        "type": "premium",
        "source": "swipesimple",
        "reference_id": "SS-OPEN",
        "status": "completed",
    }]
    row = build_ar_row(_bond(), transactions=transactions, today=date(2026, 9, 10))
    assert row["later_payments_cents"] == 0
    assert row["balance_due_cents"] == 30000


def test_refund_increases_balance():
    ledger = [
        {
            "booking_number": "BK-100",
            "type": "payment",
            "amount": -20000,
            "amount_is_cents": True,
            "timestamp": "2026-09-01T12:00:00+00:00",
        },
        {
            "booking_number": "BK-100",
            "type": "refund",
            "amount": 5000,
            "amount_is_cents": True,
            "timestamp": "2026-09-02T12:00:00+00:00",
        },
    ]
    row = build_ar_row(_bond(), ledger_entries=ledger, today=date(2026, 9, 10))
    # 500 - 200 down - 200 payment + 50 refund = 150
    assert row["balance_due_cents"] == 15000


def test_legacy_record_bond_payment_is_the_down_payment_when_field_missing():
    bond = {
        "booking_number": "BK-OLD",
        "premium": 500,
        "defendant_name": "Old Client",
    }
    payments = [{
        "booking_number": "BK-OLD",
        "amount": 500,
        "source": "retrospective_manual",
        "type": "down_payment",
    }]
    row = build_ar_row(bond, legacy_payments=payments, today=date(2026, 9, 10))
    assert row["down_payment_entered"] is True
    assert row["down_payment_cents"] == 50000
    assert row["balance_due_cents"] == 0
    assert row["ar_status"] == "paid_in_full"


def test_missing_down_payment_is_not_invented_as_zero_entry():
    bond = {"booking_number": "BK-NEW", "premium_cents": 10000, "defendant_name": "New"}
    row = build_ar_row(bond, today=date(2026, 9, 10))
    assert row["down_payment_entered"] is False
    assert row["down_payment_cents"] is None
    assert row["balance_due_cents"] == 10000


def test_friendly_tone_for_on_time_payer_who_missed_once():
    row = build_ar_row(_bond(), today=date(2026, 9, 3), inbound_texts=["Thanks, paying Friday"])
    assert row["on_time_count"] >= 1
    assert row["late_count"] == 1
    assert row["tone"] == "friendly"
    assert choose_tone(
        on_time_count=3, late_count=1, broken_promises=0, days_overdue=2, sentiment="positive",
    ) == "friendly"


def test_firm_tone_for_broken_promises_or_silence():
    bond = _bond(ar_history=[{"promise_date": "2026-08-15", "status": "sent"}])
    row = build_ar_row(bond, today=date(2026, 9, 10))
    assert row["broken_promises"] >= 1
    assert row["tone"] == "firm"
    silent = build_ar_row(
        _bond(next_payment_due="2026-08-01", down_payment_cents=0, down_payment_entered_at=None),
        today=date(2026, 9, 10),
    )
    # down_payment_cents 0 is an entered zero, no on-time payment, 40 days overdue, no replies
    assert silent["sentiment"] == "no_history"
    assert silent["days_overdue"] >= 7
    assert silent["tone"] == "firm"


def test_quiet_hours_block_outside_8am_to_9pm_eastern():
    # January is EST (UTC-5). July is EDT (UTC-4).
    assert within_contact_window(datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc)) is True   # 8:00 AM
    assert within_contact_window(datetime(2026, 1, 15, 12, 59, tzinfo=timezone.utc)) is False  # 7:59 AM
    assert within_contact_window(datetime(2026, 1, 15, 1, 59, tzinfo=timezone.utc)) is True    # 8:59 PM
    assert within_contact_window(datetime(2026, 1, 15, 2, 0, tzinfo=timezone.utc)) is False    # 9:00 PM
    assert within_contact_window(datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)) is True    # 8:00 AM EDT
    assert within_contact_window(datetime(2026, 7, 15, 11, 59, tzinfo=timezone.utc)) is False  # 7:59 AM EDT
    assert within_contact_window(datetime(2026, 7, 16, 0, 59, tzinfo=timezone.utc)) is True    # 8:59 PM EDT
    assert within_contact_window(datetime(2026, 7, 16, 1, 0, tzinfo=timezone.utc)) is False    # 9:00 PM EDT

    blocked = evaluate_reminder_send(
        now_utc=datetime(2026, 1, 15, 2, 30, tzinfo=timezone.utc),
        opted_out=False,
        recipient_role="indemnitor",
        recipient_phone="2395550142",
        defendant_phone="2395550199",
        indemnitor_phone="2395550142",
        text="The premium balance is $150.00. Please call the office.",
        prior_sends=[],
    )
    assert blocked["allowed"] is False
    assert "quiet_hours" in blocked["reasons"]

    allowed = evaluate_reminder_send(
        now_utc=datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc),  # 10:00 AM EST
        opted_out=False,
        recipient_role="indemnitor",
        recipient_phone="(239) 555-0142",
        defendant_phone="2395550199",
        indemnitor_phone="239-555-0142",
        text="The premium balance is $150.00. Please call the office.",
        prior_sends=[],
    )
    assert allowed["allowed"] is True


def test_opt_out_blocks_text_and_call_even_during_allowed_hours():
    result = evaluate_reminder_send(
        now_utc=datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc),
        opted_out=True,
        recipient_role="indemnitor",
        recipient_phone="2395550142",
        defendant_phone="2395550199",
        indemnitor_phone="2395550142",
        text="The premium balance is $150.00.",
        prior_sends=[],
    )
    assert result["allowed"] is False
    assert "opted_out" in result["reasons"]


def test_third_party_and_arrest_language_are_blocked():
    third = evaluate_reminder_send(
        now_utc=datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc),
        opted_out=False,
        recipient_role="employer",
        recipient_phone="2395550000",
        defendant_phone="2395550199",
        indemnitor_phone="2395550142",
        text="Please pay the premium.",
        prior_sends=[],
    )
    assert "third_party_blocked" in third["reasons"]

    threat = evaluate_reminder_send(
        now_utc=datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc),
        opted_out=False,
        recipient_role="defendant",
        recipient_phone="2395550199",
        defendant_phone="2395550199",
        indemnitor_phone="2395550142",
        text="Pay today or we will have you arrested and sue you.",
        prior_sends=[],
    )
    assert threat["allowed"] is False
    assert "prohibited_language" in threat["reasons"]
    assert "arrest" in threat["prohibited_hits"]


def test_template_draft_uses_real_balance_and_no_threats():
    row = build_ar_row(_bond(), today=date(2026, 9, 3))
    text = template_draft(row, recipient_name="Jordan Indemnitor", channel="text")
    assert "$300.00" in text or "300.00" in text
    assert evaluate_reminder_send(
        now_utc=datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc),
        opted_out=False,
        recipient_role="indemnitor",
        recipient_phone="2395550142",
        defendant_phone="2395550199",
        indemnitor_phone="2395550142",
        text=text,
        prior_sends=[],
    )["allowed"] is True


def test_text_rail_is_bluebubbles_on_0178_and_not_twilio_sms():
    send_src = inspect.getsource(ar_service._send_bluebubbles)
    assert "2399550178" in inspect.getsource(ar_service) or "AR_FROM_LINE" in send_src
    assert "send_sms" not in send_src
    assert "Messages.json" not in send_src
    voice_src = inspect.getsource(ar_service.place_shannon_outbound_call)
    assert "Calls.json" in voice_src
    assert "Messages.json" not in voice_src
    assert "AR_SHANNON_PLACE_CALLS" in voice_src
