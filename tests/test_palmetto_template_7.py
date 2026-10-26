"""Palmetto template 7 mapping. Template 5 stays the live default.

Template 7 is the draft clone (co-indemnitor, POA slots 5–7, Schedule A).
The DocuSeal client is stubbed. Nothing is sent. Parties and charges are
synthetic, and the only signer email is the office admin inbox.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard.services.charge_verbatim import (
    SCHEDULE_A_POINTER,
    ChargeCapacityError,
    fit_charges_for_template,
    join_charge_summary,
    template_record,
)
from dashboard.services.charge_verbatim import VerbatimCharge
from dashboard.services.docuseal_service import (
    DocuSealService,
    attach_schedule_payment_sources,
)
from dashboard.services.docuseal_signing_ux import (
    IDENTITY_READONLY_FIELD_NAMES,
    submission_fields_from_values,
)
from dashboard.services.staff_test_case import DEFAULT_SIGNER_EMAIL
from tests.test_verbatim_bond_charges import (
    CHARGE_TEXT,
    _post_finalize,
    _submission_fields,
)

ROOT = Path(__file__).resolve().parents[1]
T7_PATH = ROOT / "tests" / "fixtures" / "docuseal_t7_fields.json"
LIVE_PATH = ROOT / "dashboard" / "docuseal_live_template_fields.json"

_SIGNER_ACTS = (
    "coindemnitor_initials_1",
    "coindemnitor_initials_2",
    "coindemnitor_signature_3",
    "coindemnitor_signature_4",
    "coindemnitor_initials_schedule_a",
    "indemnitor_initials_schedule_a",
    "defendant_initials_schedule_a",
    "coindemnitor_signature_1",
    "coindemnitor_signature_2",
    "coidemnitor_signature",
)


def _t7():
    return json.loads(T7_PATH.read_text(encoding="utf-8"))


def _by_name(payload):
    found = {}
    for field in payload["fields"]:
        found.setdefault(field["name"], []).append(field)
    return found


def _charges(count):
    rows = []
    for index, text in enumerate(CHARGE_TEXT[:count], start=1):
        rows.append({
            "charge": text,
            "statute": "Fla. Stat. § 000.000",
            "degree": "2nd degree",
            "case_number": f"TEST-CASE-{index}",
            "poa_number": f"TEST-POA-{index}",
            "bond_amount": 1000,
        })
    return rows


def _bond(count, **extra):
    data = {
        "surety_id": "palmetto",
        "defendant_name": "TEST DEFENDANT",
        "indemnitor_name": "TEST INDEMNITOR",
        "coindemnitor_name": "TEST COINDEMNITOR",
        "coindemnitor_phone": "555-0100",
        "coindemnitor_email": DEFAULT_SIGNER_EMAIL,
        "coindemnitor": {
            "name": "TEST COINDEMNITOR",
            "phone": "555-0100",
            "email": DEFAULT_SIGNER_EMAIL,
        },
        "indemnitors": [
            {"name": "TEST INDEMNITOR", "email": DEFAULT_SIGNER_EMAIL, "phone": "555-0101"},
            {"name": "TEST COINDEMNITOR", "email": DEFAULT_SIGNER_EMAIL, "phone": "555-0100"},
        ],
        "county": "Lee",
        "state": "FL",
        "case_number": "TEST-CASE-1",
        "bond_amount": 1000 * count,
        "premium_amount": "100.00",
        "down_payment_amount": "40.00",
        "down_payment_method": "check",
        "down_payment_date": "2026-01-15",
        "balance_financed_amount": "60.00",
        "first_payment_due_date": "2026-02-01",
        "charges": "PLAIN STRING MUST NOT WIN",
        "charge_details": _charges(count),
    }
    data.update(extra)
    return data


def _finalize_body(count):
    return {
        "test_case": True,
        "surety_id": "palmetto",
        "booking_number": f"TEST-T7-{count}",
        "case_number": "TEST-CASE-1",
        "packet_id": f"PKT-TEST-T7-{count}",
        "defendant_name": "TEST DEFENDANT",
        "indemnitor_name": "TEST INDEMNITOR",
        "coindemnitor_name": "TEST COINDEMNITOR",
        "coindemnitor": {
            "name": "TEST COINDEMNITOR",
            "phone": "555-0100",
            "email": DEFAULT_SIGNER_EMAIL,
        },
        "bond_amount": 1000 * count,
        "premium_amount": "100.00",
        "down_payment_amount": "40.00",
        "down_payment_method": "check",
        "down_payment_date": "2026-01-15",
        "balance_financed_amount": "60.00",
        "balance_due_date": "2026-02-01",
        "county": "Lee",
        "state": "FL",
        "charges": "PLAIN STRING MUST NOT WIN",
        "charge_details": _charges(count),
        "send_email": True,
        "send_sms": True,
    }


def _assert_not_sent(payload):
    assert payload["send_email"] is False
    assert payload["send_sms"] is False
    assert payload["submitters"]
    for submitter in payload["submitters"]:
        assert submitter["email"] == DEFAULT_SIGNER_EMAIL
        assert submitter["send_email"] is False
        assert submitter["send_sms"] is False
        assert not submitter.get("phone")


def test_template_5_inventory_has_no_schedule_a_or_poa_5():
    """The live Palmetto record stays template 5, with its 200-character cap."""
    palmetto = template_record("palmetto")
    live = json.loads(LIVE_PATH.read_text(encoding="utf-8"))
    assert palmetto["template_id"] == 5
    assert palmetto["charges_summary_capacity"]["cap"] == 200
    assert "schedule_a" not in palmetto
    assert "poa_number_5" not in palmetto["field_names"]
    assert "schedule_a" not in live["5"]
    assert "poa_number_5" not in live["5"]
    assert set(live["5"]) <= set(live["7"])
    assert "coindemnitor_phone" not in live["5"]


def test_attached_field_list_is_template_7():
    """The committed list is the attached template 7 export: 221 fields, 4 roles."""
    raw = _t7()
    assert raw["id"] == 7
    assert raw["roles"] == ["bondsman", "indemnitor", "defendant", "coindemnitor"]
    assert len(raw["fields"]) == 221
    found = _by_name(raw)
    for name in ("coindemnitor_initials_1", "coindemnitor_initials_2"):
        assert found[name][0]["type"] == "initials"
        assert found[name][0]["role"] == "coindemnitor"
        assert found[name][0]["page"] == 1
    for name in ("coindemnitor_signature_3", "coindemnitor_signature_4"):
        assert found[name][0]["type"] == "signature"
        assert found[name][0]["role"] == "coindemnitor"
        assert found[name][0]["page"] == 0
    for name in (
        "coindemnitor_block_label",
        "coindemnitor_name",
        "coindemnitor_phone",
        "coindemnitor_email",
    ):
        assert found[name][0]["role"] == "bondsman"
        assert found[name][0]["type"] == "text"
        assert found[name][0]["page"] == 0
    for number in (5, 6, 7):
        field = found[f"poa_number_{number}"][0]
        assert field["role"] == "bondsman"
        assert field["page"] == 0
    assert found["schedule_a"][0]["role"] == "bondsman"
    assert found["schedule_a"][0]["page"] == 3
    for name, role in (
        ("schedule_a_label_indemnitor", "bondsman"),
        ("schedule_a_label_coindemnitor", "bondsman"),
        ("schedule_a_label_defendant", "bondsman"),
        ("indemnitor_initials_schedule_a", "indemnitor"),
        ("coindemnitor_initials_schedule_a", "coindemnitor"),
        ("defendant_initials_schedule_a", "defendant"),
    ):
        assert found[name][0]["role"] == role
        assert found[name][0]["page"] == 3


def test_template_5_prefill_does_not_gain_template_7_fields():
    """The same case aimed at template 5 keeps the combined header and no Schedule A."""
    values = DocuSealService.prefill_values_from_bond(_bond(4), template_id=5)
    assert values["charges_summary"] == ", ".join(CHARGE_TEXT[:4])
    assert values["indemnitor_name"] == "TEST INDEMNITOR / TEST COINDEMNITOR"
    assert "schedule_a" not in values
    assert "coindemnitor_phone" not in values
    assert "coindemnitor_email" not in values
    assert "coindemnitor_block_label" not in values
    assert "poa_number_5" not in values
    for name in _SIGNER_ACTS:
        assert name not in values


def test_template_7_prefill_fills_block_poas_and_schedule_a():
    """Seven charges overflow the 200-character box and land on Schedule A."""
    values = DocuSealService.prefill_values_from_bond(_bond(7), template_id=7)
    assert values["charges_summary"] == SCHEDULE_A_POINTER
    assert values["indemnitor_name"] == "TEST INDEMNITOR"
    assert values["coindemnitor_name"] == "TEST COINDEMNITOR"
    assert values["coindemnitor_phone"] == "555-0100"
    assert values["coindemnitor_email"] == DEFAULT_SIGNER_EMAIL
    assert values["coindemnitor_block_label"] == "Co-Indemnitor"
    assert values["schedule_a_label_indemnitor"] == "TEST INDEMNITOR"
    assert values["schedule_a_label_coindemnitor"] == "TEST COINDEMNITOR"
    assert values["schedule_a_label_defendant"] == "TEST DEFENDANT"
    for index, text in enumerate(CHARGE_TEXT[:7], start=1):
        assert values[f"poa_number_{index}"] == f"TEST-POA-{index}"
        assert text in values["schedule_a"]
        assert f"POA TEST-POA-{index}" in values["schedule_a"]
    assert "PLAIN STRING" not in values["schedule_a"]
    assert "Total bond: $7,000.00" in values["schedule_a"]
    assert "Premium: $100.00" in values["schedule_a"]
    assert "Amount paid: $0.00" in values["schedule_a"]
    assert "Amount paid: $40.00" not in values["schedule_a"]
    assert "check" not in values["schedule_a"]
    assert "2026-01-15" not in values["schedule_a"]
    assert "Balance due: $100.00 due 2026-02-01" in values["schedule_a"]
    assert "Balance due: $60.00" not in values["schedule_a"]
    for name in _SIGNER_ACTS:
        assert name not in values


def test_template_7_short_join_stays_in_charges_summary():
    """Six of the sample charges fit in 200 characters, so the box keeps the join."""
    values = DocuSealService.prefill_values_from_bond(_bond(6), template_id=7)
    joined = ", ".join(CHARGE_TEXT[:6])
    assert len(joined) <= 200
    assert values["charges_summary"] == joined
    assert CHARGE_TEXT[5] in values["schedule_a"]
    assert "poa_number_7" not in values


def test_template_5_still_uses_the_character_cap_not_seven_rows():
    """Eight short charges still fit template 5. Template 7 refuses the eighth."""
    rows = [VerbatimCharge(charge=f"CHARGE {index}") for index in range(1, 9)]
    placed = fit_charges_for_template(rows, surety_id="palmetto", template_id=5)
    assert placed.summary == join_charge_summary(rows)
    with pytest.raises(ChargeCapacityError) as exc:
        fit_charges_for_template(rows, surety_id="palmetto", template_id=7)
    assert exc.value.capacity == 7
    assert exc.value.unit == "rows"
    assert exc.value.charge_count == 8


def test_pointer_starts_at_201_characters():
    fitting = fit_charges_for_template(
        [VerbatimCharge(charge="A" * 200)],
        surety_id="palmetto",
        template_id=7,
    )
    assert fitting.summary == "A" * 200
    overflow = fit_charges_for_template(
        [VerbatimCharge(charge="B" * 201)],
        surety_id="palmetto",
        template_id=7,
    )
    assert overflow.summary == SCHEDULE_A_POINTER
    with pytest.raises(ChargeCapacityError) as exc:
        fit_charges_for_template(
            [VerbatimCharge(charge="C" * 201)],
            surety_id="palmetto",
            template_id=5,
        )
    assert exc.value.unit == "characters"
    assert exc.value.capacity == 200


def test_finalize_template_5_still_rejects_the_seven_charge_join(monkeypatch):
    response, captured, stores = _post_finalize(
        monkeypatch,
        _finalize_body(7),
        palmetto_template_id="5",
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "charge_capacity_exceeded"
    assert body["capacity_unit"] == "characters"
    assert body["capacity"] == 200
    assert body["charge_count"] == 7
    assert captured == []
    assert stores.get("paperwork_packets") is None or stores["paperwork_packets"].inserts == []


def test_finalize_template_7_stages_seven_charges_without_sending(monkeypatch):
    """Schedule A, POAs 1–7, and the co-indemnitor role. Send flags stay off."""
    response, captured, _stores = _post_finalize(
        monkeypatch,
        _finalize_body(7),
        palmetto_template_id="7",
    )
    assert response.status_code == 200, response.text
    payload, fields = _submission_fields(captured)
    assert payload["template_id"] == 7
    _assert_not_sent(payload)
    roles = [item["role"] for item in payload["submitters"]]
    assert roles.count("coindemnitor") == 1
    assert "indemnitor" in roles and "defendant" in roles and "bondsman" in roles
    assert fields["charges_summary"] == SCHEDULE_A_POINTER
    assert fields["coindemnitor_name"] == "TEST COINDEMNITOR"
    assert fields["coindemnitor_email"] == DEFAULT_SIGNER_EMAIL
    assert fields["coindemnitor_block_label"] == "Co-Indemnitor"
    assert fields["indemnitor_name"] == "TEST INDEMNITOR"
    assert "coindemnitor_phone" not in fields
    for index, text in enumerate(CHARGE_TEXT[:7], start=1):
        assert fields[f"poa_number_{index}"] == f"TEST-POA-{index}"
        assert text in fields["schedule_a"]
        assert f"POA TEST-POA-{index}" in fields["schedule_a"]
    assert "Total bond: $7,000.00" in fields["schedule_a"]
    assert "PLAIN STRING" not in fields["schedule_a"]
    assert "Amount paid: $0.00" in fields["schedule_a"]
    assert "Amount paid: $40.00" not in fields["schedule_a"]
    assert "Premium: $700.00" in fields["schedule_a"]
    assert "Balance due: $700.00" in fields["schedule_a"]
    for name in _SIGNER_ACTS:
        assert name not in fields
    assert "offense_1" not in fields


def test_finalize_template_7_rejects_eight_charges(monkeypatch):
    response, captured, stores = _post_finalize(
        monkeypatch,
        _finalize_body(8),
        palmetto_template_id="7",
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "charge_capacity_exceeded"
    assert body["capacity_unit"] == "rows"
    assert body["capacity"] == 7
    assert body["charge_count"] == 8
    assert captured == []
    assert stores.get("paperwork_packets") is None or stores["paperwork_packets"].inserts == []


def _ledger_payment(**extra):
    row = {
        "booking_number": "TEST-T7-PAID",
        "type": "payment",
        "entry_kind": "payment",
        "amount": -4000,
        "amount_is_cents": True,
        "method": "check",
        "timestamp": "2026-01-15T18:00:00+00:00",
        "category": "premium",
        "source": "financial_ledger",
    }
    row.update(extra)
    return row


def test_schedule_a_ignores_write_time_down_payment():
    """A planned down payment is not a receipt, and the balance stays the premium."""
    values = DocuSealService.prefill_values_from_bond(
        _bond(
            1,
            booking_number="TEST-T7-PLAN",
            amount_paid="40.00",
            payment_method="check",
            paid_at="2026-01-15T18:00:00+00:00",
            numeric_balance_due="60.00",
            balance_due="60.00",
        ),
        template_id=7,
    )
    schedule = values["schedule_a"]
    assert "Amount paid: $0.00" in schedule
    assert "Amount paid: $40.00" not in schedule
    assert "check" not in schedule
    assert "2026-01-15" not in schedule
    assert "Balance due: $100.00 due 2026-02-01" in schedule
    assert "$60.00" not in schedule


def test_schedule_a_prints_confirmed_ledger_payment():
    bond = _bond(
        7,
        booking_number="TEST-T7-PAID",
        ledger_entries=[_ledger_payment()],
    )
    values = DocuSealService.prefill_values_from_bond(bond, template_id=7)
    schedule = values["schedule_a"]
    assert "Amount paid: $40.00 check 2026-01-15" in schedule
    assert "Balance due: $60.00 due 2026-02-01" in schedule
    assert "down_payment" not in schedule


def test_schedule_a_prints_each_confirmed_payment():
    entries = [
        _ledger_payment(amount=-2500, method="cash", timestamp="2026-01-10T12:00:00+00:00"),
        _ledger_payment(amount=-1500, method="check", timestamp="2026-01-15T18:00:00+00:00"),
    ]
    values = DocuSealService.prefill_values_from_bond(
        _bond(1, booking_number="TEST-T7-PAID", ledger_entries=entries),
        template_id=7,
    )
    schedule = values["schedule_a"]
    assert "Amount paid: $25.00 cash 2026-01-10" in schedule
    assert "Amount paid: $15.00 check 2026-01-15" in schedule
    assert schedule.index("2026-01-10") < schedule.index("2026-01-15")
    assert "Balance due: $60.00 due 2026-02-01" in schedule


def test_schedule_a_skips_unconfirmed_and_write_bond_down_rows():
    booking = "TEST-T7-PAID"
    entries = [
        _ledger_payment(
            type="down_payment",
            entry_kind="down_payment",
            stripe_swipe_ref=f"write-bond-down:{booking}",
        ),
        _ledger_payment(
            type="payment",
            entry_kind="payment",
            stripe_swipe_ref=f"write-bond-down:{booking}",
            amount=-4000,
        ),
    ]
    payments = [
        {
            "booking_number": booking,
            "amount": 40.00,
            "method": "check",
            "status": "pending",
            "type": "payment",
            "timestamp": "2026-01-15T18:00:00+00:00",
        },
        {
            "booking_number": booking,
            "amount": 40.00,
            "method": "cash",
            "status": "declined",
            "type": "payment",
            "timestamp": "2026-01-16T18:00:00+00:00",
        },
    ]
    transactions = [
        {
            "booking_number": booking,
            "amount": 40.00,
            "method": "card",
            "status": "",
            "type": "premium",
            "timestamp": "2026-01-17T18:00:00+00:00",
        },
    ]
    values = DocuSealService.prefill_values_from_bond(
        _bond(
            1,
            booking_number=booking,
            ledger_entries=entries,
            payments=payments,
            transactions=transactions,
        ),
        template_id=7,
    )
    schedule = values["schedule_a"]
    assert "Amount paid: $0.00" in schedule
    assert "check" not in schedule
    assert "cash" not in schedule
    assert "card" not in schedule
    assert "Balance due: $100.00 due 2026-02-01" in schedule


def test_schedule_a_prints_a_completed_payment_row():
    payment = {
        "booking_number": "TEST-T7-PAID",
        "amount": 40.00,
        "method": "check",
        "status": "completed",
        "type": "payment",
        "timestamp": "2026-01-15T18:00:00+00:00",
        "transaction_id": "TEST-TXN-1",
    }
    values = DocuSealService.prefill_values_from_bond(
        _bond(1, booking_number="TEST-T7-PAID", payments=[payment]),
        template_id=7,
    )
    schedule = values["schedule_a"]
    assert "Amount paid: $40.00 check 2026-01-15" in schedule
    assert "Balance due: $60.00 due 2026-02-01" in schedule


class _Rows:
    def __init__(self, docs):
        self.docs = list(docs)
        self.queries = []

    def find(self, query):
        self.queries.append(query)
        return self

    async def to_list(self, length=None):
        return list(self.docs)


@pytest.mark.asyncio
async def test_packet_path_reads_the_ledger_and_not_the_plan(monkeypatch):
    """The paperwork display read uses the ledger. A failed read stays at $0."""
    ledger = _Rows([_ledger_payment()])
    payments = _Rows([])
    transactions = _Rows([])
    monkeypatch.setattr(
        "dashboard.extensions.get_collection",
        lambda name: {
            "financial_ledger": ledger,
            "payments": payments,
            "transactions": transactions,
        }[name],
    )
    bond = _bond(7, booking_number="TEST-T7-PAID")
    await attach_schedule_payment_sources(bond, 7)
    values = DocuSealService.prefill_values_from_bond(bond, template_id=7)
    assert ledger.queries and "TEST-T7-PAID" in ledger.queries[0]["booking_number"]["$in"]
    assert "Amount paid: $40.00 check 2026-01-15" in values["schedule_a"]
    assert "Balance due: $60.00 due 2026-02-01" in values["schedule_a"]

    def _boom(name):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr("dashboard.extensions.get_collection", _boom)
    unpaid = _bond(7, booking_number="TEST-T7-PAID")
    await attach_schedule_payment_sources(unpaid, 7)
    missed = DocuSealService.prefill_values_from_bond(unpaid, template_id=7)
    assert "Amount paid: $0.00" in missed["schedule_a"]
    assert "Amount paid: $40.00" not in missed["schedule_a"]
    assert "Balance due: $100.00 due 2026-02-01" in missed["schedule_a"]


@pytest.mark.asyncio
async def test_template_5_does_not_read_the_ledger(monkeypatch):
    def _boom(name):
        raise AssertionError(name)

    monkeypatch.setattr("dashboard.extensions.get_collection", _boom)
    bond = _bond(1, booking_number="TEST-T7-PAID")
    await attach_schedule_payment_sources(bond, 5)
    assert "ledger_entries" not in bond


def test_in_person_locks_coindemnitor_identity_fields():
    for name in ("coindemnitor_name", "coindemnitor_phone", "coindemnitor_email"):
        assert name in IDENTITY_READONLY_FIELD_NAMES
    values = {
        "coindemnitor_name": "TEST COINDEMNITOR",
        "coindemnitor_phone": "555-0100",
        "coindemnitor_email": DEFAULT_SIGNER_EMAIL,
        "indemnitor_name": "TEST INDEMNITOR",
        "defendant_name": "TEST DEFENDANT",
    }
    locked = {
        field["name"]: field
        for field in submission_fields_from_values(
            values, extra_readonly=IDENTITY_READONLY_FIELD_NAMES,
        )
    }
    opened = {
        field["name"]: field
        for field in submission_fields_from_values(values)
    }
    for name in ("coindemnitor_name", "coindemnitor_phone", "coindemnitor_email"):
        assert locked[name]["readonly"] is True
        assert locked[name]["default_value"] == values[name]
        assert opened[name]["readonly"] is False
    assert locked["indemnitor_name"]["readonly"] is True
    assert locked["defendant_name"]["readonly"] is True


def test_completed_down_payment_receipt_counts_once():
    """A collected down-payment row is a receipt. The write-time plan is not added again."""
    booking = "TEST-T7-PAID"
    payment = {
        "booking_number": booking,
        "plan_id": "TEST-PLAN-1",
        "amount": 40.00,
        "method": "Cash",
        "type": "down_payment",
        "transaction_id": "",
        "status": "completed",
        "timestamp": "2026-01-15T18:00:00+00:00",
        "county": "Lee",
        "state": "FL",
        "case_number": "TEST-CASE-1",
    }
    placeholder = _ledger_payment(
        type="down_payment",
        entry_kind="down_payment",
        source="write_bond",
        stripe_swipe_ref=f"write-bond-down:{booking}",
        amount=-4000,
    )
    values = DocuSealService.prefill_values_from_bond(
        _bond(1, booking_number=booking, payments=[payment], ledger_entries=[placeholder]),
        template_id=7,
    )
    schedule = values["schedule_a"]
    assert schedule.count("Amount paid:") == 1
    assert "Amount paid: $40.00 Cash 2026-01-15" in schedule
    assert "Balance due: $60.00 due 2026-02-01" in schedule


def test_other_county_receipt_is_excluded():
    booking = "TEST-T7-PAID"
    ours = {
        "booking_number": booking,
        "amount": 25.00,
        "method": "check",
        "type": "payment",
        "status": "completed",
        "timestamp": "2026-01-15T18:00:00+00:00",
        "county": "Lee",
        "state": "FL",
        "case_number": "TEST-CASE-1",
    }
    other = {
        "booking_number": booking,
        "amount": 40.00,
        "method": "cash",
        "type": "payment",
        "status": "completed",
        "timestamp": "2026-01-15T18:00:00+00:00",
        "county": "Collier",
        "state": "FL",
        "case_number": "TEST-CASE-OTHER",
    }
    values = DocuSealService.prefill_values_from_bond(
        _bond(1, booking_number=booking, payments=[ours, other]),
        template_id=7,
    )
    schedule = values["schedule_a"]
    assert "Amount paid: $25.00 check 2026-01-15" in schedule
    assert "$40.00" not in schedule
    assert "cash" not in schedule
    assert "Balance due: $75.00 due 2026-02-01" in schedule


def test_unbound_receipt_drops_when_another_case_shares_the_booking():
    booking = "TEST-T7-PAID"
    unbound = {
        "booking_number": booking,
        "amount": 15.00,
        "method": "check",
        "type": "payment",
        "status": "completed",
        "timestamp": "2026-01-15T18:00:00+00:00",
    }
    other = {
        "booking_number": booking,
        "amount": 40.00,
        "method": "cash",
        "type": "payment",
        "status": "completed",
        "timestamp": "2026-01-15T18:00:00+00:00",
        "county": "Collier",
        "state": "FL",
    }
    values = DocuSealService.prefill_values_from_bond(
        _bond(1, booking_number=booking, payments=[unbound, other]),
        template_id=7,
    )
    schedule = values["schedule_a"]
    assert "Amount paid: $0.00" in schedule
    assert "$15.00" not in schedule
    assert "$40.00" not in schedule
    assert "Balance due: $100.00 due 2026-02-01" in schedule


def test_receipt_without_a_case_scope_fails_closed():
    values = DocuSealService.prefill_values_from_bond(
        _bond(
            1,
            booking_number="TEST-T7-PAID",
            county="",
            case_number="",
            ledger_entries=[_ledger_payment()],
        ),
        template_id=7,
    )
    schedule = values["schedule_a"]
    assert "Amount paid: $0.00" in schedule
    assert "Amount paid: $40.00" not in schedule
    assert "Balance due: $100.00 due 2026-02-01" in schedule


def test_receipt_date_uses_eastern_calendar_day():
    """01:30 UTC is still the previous evening in the office timezone."""
    payment = _ledger_payment(timestamp="2026-01-16T04:30:00+00:00")
    values = DocuSealService.prefill_values_from_bond(
        _bond(1, booking_number="TEST-T7-PAID", ledger_entries=[payment]),
        template_id=7,
    )
    schedule = values["schedule_a"]
    assert "Amount paid: $40.00 check 2026-01-15" in schedule
    assert "2026-01-16" not in schedule.split("Balance due:")[0]


def test_poa_change_does_not_change_cosigner_identity(monkeypatch):
    """The co-indemnitor submitter key is the packet id, not the POA number."""
    import asyncio

    import httpx

    captured = []
    svc = DocuSealService(base_url="https://sign.example.invalid", api_key="test-not-a-real-key")

    async def _create(self, **kwargs):
        captured.append(kwargs)
        return {"id": 1, "submitters": []}

    async def _blocked(self, method, url, *args, **kwargs):
        raise AssertionError("live DocuSeal request")

    svc.create_submission = _create.__get__(svc, DocuSealService)
    monkeypatch.setattr(httpx.AsyncClient, "request", _blocked)

    base = {
        "surety_id": "palmetto",
        "booking_number": "TEST-BK-POA",
        "defendant_name": "TEST DEFENDANT",
        "indemnitor_name": "TEST INDEMNITOR",
        "county": "Lee",
        "state": "FL",
        "case_number": "TEST-CASE-1",
        "bond_case_id": "TEST-BOND-1",
        "match_id": "TEST-MATCH-1",
        "match_status": "validated",
        "defendant_id": "TEST-DEF-1",
        "indemnitor_id": "TEST-IND-1",
        "bond_amount": 1000,
        "premium_amount": 100,
        "indemnitors": [
            {"name": "TEST INDEMNITOR", "email": DEFAULT_SIGNER_EMAIL},
            {"name": "TEST COINDEMNITOR", "email": DEFAULT_SIGNER_EMAIL, "role": "co_indemnitor"},
        ],
    }

    async def _once(poa):
        data = dict(base, poa_number=poa)
        await svc.create_submission_for_packet(
            template_id=7,
            packet_id="PKT-TEST-POA1",
            bond_data=data,
            indemnitors=data["indemnitors"],
            defendant={"name": "TEST DEFENDANT", "email": DEFAULT_SIGNER_EMAIL},
            staff_test_case=True,
            send_email=True,
        )

    asyncio.run(_once("TEST-POA-AAAA"))
    asyncio.run(_once("TEST-POA-BBBB"))
    ids = []
    for payload in captured:
        assert payload["send_email"] is False
        assert payload["send_sms"] is False
        co = next(item for item in payload["submitters"] if item["role"] == "coindemnitor")
        assert co["send_email"] is False
        assert "TEST-POA-" not in co["external_id"]
        ids.append(co["external_id"])
    assert ids == ["PKT-TEST-POA1:indemnitor:1", "PKT-TEST-POA1:indemnitor:1"]
