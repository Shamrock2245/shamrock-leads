"""In-memory counts for the open-BondCase identity tally. No Mongo."""
from __future__ import annotations

from pathlib import Path

from scripts.count_unverified_open_bondcases import main
from dashboard.services.identity_verification_service import (
    OPEN_BOND_CASE_STATUSES,
    summarize_open_bond_cases,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "count_unverified_open_bondcases.py"


def _scan(name, *, success=True, state="FL"):
    return {
        "success": success,
        "extracted": {
            "full_name": name,
            "first_name": name.split()[0],
            "last_name": name.split()[-1],
            "dl_state": state,
        },
    }


def _case(status, name="Jamie Sample", **extra):
    row = {
        "_collection": "bond_cases",
        "status": status,
        "bond_case_id": extra.pop("bond_case_id", "BC-1"),
        "booking_number": extra.pop("booking_number", "BK-1"),
        "indemnitor_name": name,
        "indemnitor": {"name": name},
    }
    row.update(extra)
    return row


def _attest(name, bond_case_id="BC-1"):
    return {
        "action": "staff_id_attestation",
        "actor": "office@example.invalid",
        "timestamp": "2026-10-09T00:00:00+00:00",
        "details": {
            "indemnitor_name": name,
            "id_type": "drivers_license",
            "issuing_state": "GA",
            "id_last4": "4821",
            "bond_case_id": bond_case_id,
            "timestamp": "2026-10-09T00:00:00+00:00",
            "staff_user": "office@example.invalid",
        },
    }


def test_open_statuses_match_the_kanban():
    assert OPEN_BOND_CASE_STATUSES == frozenset({"active", "monitoring", "alert", "reinstated"})


def test_counts_only_open_cases_and_splits_reasons():
    cases = [
        _case("active", bond_case_id="BC-SCAN", booking_number="BK-SCAN", id_scan=_scan("Jamie Sample")),
        _case("monitoring", "Robin Sample", bond_case_id="BC-ATT", booking_number="BK-ATT"),
        _case("alert", bond_case_id="BC-NONE", booking_number="BK-NONE"),
        _case("reinstated", bond_case_id="BC-FAIL", booking_number="BK-FAIL", id_scan=_scan("Jamie Sample", success=False)),
        _case(
            "active",
            bond_case_id="BC-MIS",
            booking_number="BK-MIS",
            id_scan=_scan("Other Person"),
        ),
        _case("exonerated", bond_case_id="BC-CLOSED", booking_number="BK-CLOSED"),
        _case("forfeited", bond_case_id="BC-FORF", booking_number="BK-FORF"),
        _case("surrendered", bond_case_id="BC-SUR", booking_number="BK-SUR"),
        _case("active", bond_case_id="BC-TEST", booking_number="TEST-SKIP", is_test=True),
        _case("pending", bond_case_id="BC-PEND", booking_number="BK-PEND"),
    ]
    # Mirror row must not double-count the scan-verified case.
    cases.append({
        "_collection": "active_bonds",
        "status": "active",
        "bond_case_id": "BC-SCAN",
        "booking_number": "BK-SCAN",
        "indemnitor": {"name": "Jamie Sample"},
    })
    counts = summarize_open_bond_cases(cases, [], [_attest("Robin Sample", "BC-ATT")])
    assert counts["total_open"] == 5
    assert counts["verified_by_scan"] == 1
    assert counts["verified_by_attestation"] == 1
    assert counts["blocked_no_scan"] == 1
    assert counts["blocked_scan_failed"] == 1
    assert counts["blocked_name_mismatch"] == 1
    assert counts["blocked_lookup_failed"] == 0
    blocked = (
        counts["blocked_no_scan"]
        + counts["blocked_scan_failed"]
        + counts["blocked_name_mismatch"]
        + counts["blocked_lookup_failed"]
    )
    assert counts["verified_by_scan"] + counts["verified_by_attestation"] + blocked == counts["total_open"]
    blob = str(counts)
    assert "Jamie" not in blob
    assert "4821" not in blob


def test_script_refuses_without_the_read_only_uri(monkeypatch):
    monkeypatch.delenv("SHAMROCK_MONGO_RO_URI", raising=False)
    monkeypatch.setenv("MONGODB_URI", "mongodb://should-not-be-used")
    assert main() == 2


def test_script_is_read_only_and_ignores_the_write_uri():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "SHAMROCK_MONGO_RO_URI" in text
    assert "MONGODB_URI" not in text
    for banned in ("insert_one", "insert_many", "update_one", "update_many", "delete_one", "delete_many", "replace_one"):
        assert banned not in text
    assert "os.environ.get" in text
