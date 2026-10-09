"""Home FL counties: matrix state, the smoke/relay evidence slot and its gates.

No network. ``docs/recon/smoke_evidence.json`` is the Leads Ops handoff for
Lee / Collier write smokes and Charlotte / Manatee relay evidence. The builder
must refuse rows that would make a county look smoked when it is not.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "docs" / "recon" / "smoke_evidence.json"
HOME = ("Lee", "Collier", "Charlotte", "Manatee", "Hendry", "Glades", "DeSoto")


def _builder():
    spec = importlib.util.spec_from_file_location("build_recon_matrix", ROOT / "scripts" / "build_recon_matrix.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


B = _builder()


def _build(smoke_path):
    return B.build_matrix(B.DEFAULT_INVENTORY, B.DEFAULT_EVIDENCE, B.DEFAULT_LIVE_EVIDENCE, smoke_path)


def _matrix_status(text: str, county: str) -> str:
    for line in text.splitlines():
        if line.startswith(f"| FL | ") and f"| {county} County |" in line:
            return line.split("|")[6].strip()
    raise AssertionError(county)


def _write(tmp_path, records):
    p = tmp_path / "smoke.json"
    p.write_text(json.dumps({"records": records}))
    return p


BASE = {
    "requested_on": "2026-10-08",
    "command": "python main.py Lee",
    "expect": "status ok",
    "source": "docs/recon/FL_HOME_COUNTIES_SOURCE_CONTRACT_2026-10-08.md",
}


def _passed_write(label="Lee (FL)", **result):
    res = {"status": "ok", "records_scraped": 120, "new_records": 3, "updated_records": 117,
           "mongo_writer_results": 1}
    res.update(result)
    return {**BASE, "label": label, "kind": "write_smoke", "status": "passed", "run_on": "2026-10-09",
            "commit": "abc1234", "egress": "mac", "method": "one_shot_run", "result": res}


def _passed_relay_read(label="Manatee (FL)", **over):
    row = {**BASE, "label": label, "kind": "relay_read", "status": "passed", "run_on": "2026-10-09",
           "commit": "abc1234", "egress": "residential_relay", "method": "relay_read_smoke",
           "result": {"status": "ok", "bookings": 400, "columns": ["Booking #", "Released"]}}
    row.update(over)
    return row


# ── Committed state ─────────────────────────────────────────────────────────
def test_home_counties_matrix_state_after_source_checks():
    text, summary = _build(B.DEFAULT_SMOKE_EVIDENCE)
    for county in ("Lee", "Collier", "Glades"):
        assert _matrix_status(text, county) == "candidate_productive", county
    assert _matrix_status(text, "DeSoto") == "recon_only"
    # Hendry: MyOCV feed has only a person-level MNI id, no booking number (#143).
    # Charlotte / Manatee: Revize roster challenged from every exit (2026-10-09).
    for county in ("Hendry", "Charlotte", "Manatee"):
        assert _matrix_status(text, county) == "fail_closed", county
    # 2026-10-08: main also moved Orange (#134) and Indian River (#136) to candidate_productive;
    # #143 moved Hendry from recon_only to fail_closed; 2026-10-09 Miami-Dade too (no booking number).
    # 2026-10-09 Charlotte and Manatee recon_only -> fail_closed.
    assert "| FL | 67 | 67 | 4 | 20 | 22 | 0 | 21 |" in text


def test_no_home_county_is_promoted_in_health():
    from dashboard.extensions import SCRAPER_SOURCE_STATES

    for county in HOME:
        state = SCRAPER_SOURCE_STATES.get(f"{county} (FL)", "unverified")
        assert state != "verified_public", county
        # Hendry is held fail_closed (#143: person-level MNI id, no booking number);
        # Charlotte / Manatee since 2026-10-09 (Revize roster challenged from every exit).
        held = ("Hendry", "Charlotte", "Manatee")
        assert state == ("fail_closed" if county in held else "unverified"), county


def test_committed_smoke_rows_are_requests_only_until_leads_ops_reports():
    rows = json.loads(SMOKE.read_text())["records"]
    by = {(r["label"], r["kind"]) for r in rows}
    assert ("Lee (FL)", "write_smoke") in by
    assert ("Collier (FL)", "write_smoke") in by
    assert ("Glades (FL)", "write_smoke") in by
    for r in rows:
        if r["label"] in ("Collier (FL)", "Glades (FL)"):
            assert r["blocked_on"].startswith("HOLD")
    for county in ("Charlotte (FL)", "Manatee (FL)"):
        assert (county, "relay_read") in by and (county, "relay_write") in by
    for r in rows:
        assert r["status"] == "requested"
        assert not r.get("result") and not r.get("run_on") and not r.get("commit")
    text, _ = _build(B.DEFAULT_SMOKE_EVIDENCE)
    assert "## Write smoke and relay evidence" in text
    assert "| Collier (FL) | unverified | write_smoke | requested | 2026-10-08 |" in text


def test_committed_matrix_matches_a_fresh_build():
    text, _ = _build(B.DEFAULT_SMOKE_EVIDENCE)
    assert B.DEFAULT_OUTPUT.read_text() == text


# ── Gates ───────────────────────────────────────────────────────────────────
def test_valid_passed_rows_render(tmp_path):
    text, _ = _build(_write(tmp_path, [_passed_write(), _passed_relay_read()]))
    assert "| Lee (FL) | unverified | write_smoke | passed | 2026-10-09 | abc1234 | mac |" in text
    assert "columns Booking # / Released" in text


@pytest.mark.parametrize("mutate,match", [
    (lambda r: r.update(result={"status": "ok"}), "carries no run_on"),
    (lambda r: r.update(label="Nowhere (FL)"), "not registered"),
    (lambda r: r.update(kind="vibes"), "unknown smoke kind"),
    (lambda r: r.update(kind="relay_read"), "only for RELAY_ONLY_LABELS"),
    (lambda r: r.update(requested_on="10/08/2026"), "YYYY-MM-DD"),
    (lambda r: r.update(command=""), "command is required"),
])
def test_bad_requested_rows_are_refused(tmp_path, mutate, match):
    row = {**BASE, "label": "Lee (FL)", "kind": "write_smoke", "status": "requested"}
    mutate(row)
    with pytest.raises(RuntimeError, match=match):
        _build(_write(tmp_path, [row]))


def test_a_write_without_a_mongo_writer_does_not_count(tmp_path):
    # No MONGODB_URI: BaseScraper still reports new_records = len(records).
    with pytest.raises(RuntimeError, match="Mongo writer result"):
        _build(_write(tmp_path, [_passed_write(mongo_writer_results=0)]))
    with pytest.raises(RuntimeError, match="Mongo writer result"):
        _build(_write(tmp_path, [_passed_write(new_records=0, updated_records=0)]))
    with pytest.raises(RuntimeError, match="Mongo writer result"):
        _build(_write(tmp_path, [_passed_write(status="error")]))


def _prod_aggregate(**result):
    res = {"status": "ok", "rows": 240, "window_hours": 24, "booking_number_blank": 0,
           "booking_number_duplicates": 0, "booking_number_shapes": {"NNNNNNN": 240}}
    res.update(result)
    return {**BASE, "label": "Lee (FL)", "kind": "write_smoke", "status": "passed", "run_on": "2026-10-09",
            "commit": "abc1234", "egress": "vps", "method": "prod_mongo_aggregate", "result": res}


def test_documented_prod_aggregate_evidence_is_accepted(tmp_path):
    text, _ = _build(_write(tmp_path, [_prod_aggregate()]))
    assert "| Lee (FL) | unverified | write_smoke | passed | 2026-10-09 | abc1234 | vps |" in text


@pytest.mark.parametrize("over", [
    {"rows": 0}, {"status": "no_rows"}, {"window_hours": 168}, {"window_hours": None},
    {"booking_number_blank": 3}, {"booking_number_duplicates": 2},
])
def test_prod_aggregate_evidence_has_its_own_gate(tmp_path, over):
    with pytest.raises(RuntimeError, match="prod aggregate"):
        _build(_write(tmp_path, [_prod_aggregate(**over)]))


def test_prod_aggregate_is_not_relay_evidence(tmp_path):
    row = _prod_aggregate()
    row.update(label="Pinellas (FL)", kind="relay_write", egress="residential_relay")
    with pytest.raises(RuntimeError, match="only for write_smoke"):
        _build(_write(tmp_path, [row]))


def test_relay_evidence_must_come_from_the_residential_relay(tmp_path):
    with pytest.raises(RuntimeError, match="residential_relay"):
        _build(_write(tmp_path, [_passed_relay_read(egress="vps")]))
    with pytest.raises(RuntimeError, match="only for RELAY_ONLY_LABELS"):
        _build(_write(tmp_path, [_passed_relay_read(label="Lee (FL)")]))


def test_relay_read_must_record_the_live_header_set(tmp_path):
    row = _passed_relay_read()
    row["result"] = {"status": "ok", "bookings": 400}
    with pytest.raises(RuntimeError, match="live header set"):
        _build(_write(tmp_path, [row]))
    row["result"] = {"status": "ok", "bookings": 0, "columns": ["Booking #"]}
    with pytest.raises(RuntimeError, match="bookings >= 1"):
        _build(_write(tmp_path, [row]))


def test_dates_commit_and_method_are_required_on_a_result(tmp_path):
    for over, match in (
        ({"run_on": "2026-10-07"}, "on or after requested_on"),
        ({"run_on": None}, "run_on"),
        ({"commit": "main"}, "commit"),
        ({"egress": "proxy"}, "egress"),
        ({"method": "vibes"}, "method"),
    ):
        row = _passed_write()
        row.update(over)
        with pytest.raises(RuntimeError, match=match):
            _build(_write(tmp_path, [row]))


def test_fail_closed_scope_cannot_have_a_passed_write(tmp_path):
    with pytest.raises(RuntimeError, match="fail_closed scope"):
        _build(_write(tmp_path, [_passed_write(label="Sarasota (FL)")]))


def test_verified_public_needs_a_passed_write_when_listed(tmp_path):
    row = {**BASE, "label": "Broward (FL)", "kind": "write_smoke", "status": "requested"}
    with pytest.raises(RuntimeError, match="verified_public with no passed write"):
        _build(_write(tmp_path, [row]))
    _build(_write(tmp_path, [row, _passed_write(label="Broward (FL)")]))


def test_duplicate_rows_are_refused(tmp_path):
    with pytest.raises(RuntimeError, match="duplicate"):
        _build(_write(tmp_path, [_passed_write(), copy.deepcopy(_passed_write())]))


# ── Read-only aggregate script ──────────────────────────────────────────────
def test_smoke_evidence_check_summarizes_without_pii():
    spec = importlib.util.spec_from_file_location("sec", ROOT / "scripts" / "smoke_evidence_check.py")
    sec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sec)
    docs = [
        {"booking_number": "1033660", "full_name": "DOE, JANE", "dob": "01/01/1990", "address": "1 Main St",
         "bond_amount_raw": "", "charges": "BATTERY | DUI", "booking_date": "2026-10-08",
         "booking_time": "06:08", "status": "In Custody", "mugshot_url": "https://x/y.jpg",
         "scraped_at": "2026-10-08T10:00:00"},
        {"booking_number": "1033661", "bond_amount_raw": "2500.00", "bond_type": "SURETY",
         "charges": "THEFT", "booking_date": "2026-10-08", "status": "Released", "release_date": "2026-10-08"},
        {"booking_number": "1033661", "bond_amount": 0, "charges": ""},
        {"booking_number": "", "bond_amount_raw": "0"},
    ]
    out = sec.summarize(docs)
    assert out["rows"] == 4
    assert out["booking_number_blank"] == 1
    assert out["booking_number_duplicates"] == 1
    assert out["booking_number_shapes"] == {"NNNNNNN": 3}
    assert out["bond"] == {"empty": 1, "positive": 1, "zero": 2}
    assert out["multi_charge"] == 1 and out["with_charges"] == 2
    blob = json.dumps(out)
    for secret in ("DOE", "JANE", "1990", "Main St", "1033660"):
        assert secret not in blob
    q = sec.county_query("St. Johns", "FL", __import__("datetime").datetime(2026, 10, 8))
    assert q["$and"][0]["county"]["$regex"] == "^St\\.\\ Johns(?:\\s+County)?$"


def test_relay_read_walk_meta_carries_column_names_only():
    from scrapers.counties.charlotte import ROSTER
    from scrapers.counties.manatee import walk_roster

    headers = ["Booking #", "Last Name", "First Name", "Middle", "Charge", "Arrest Date", "Released"]
    page = {"has_table": True, "headers": headers, "text": "", "next_href": "", "max_page": 1,
            "title": "Bookings",
            "rows": [{"cells": ["2026012345", "DOE", "JANE", "Q", "DUI", "10/06/2026 14:35", ""],
                      "href": "/bookings/2026012345", "img": ""}]}
    _recs, meta = ROSTER.walk(lambda url, pg: page, sleep=lambda s: None)
    assert meta["columns"] == headers
    _recs, meta = walk_roster(lambda url, pg: page, sleep=lambda s: None)
    assert meta["columns"] == headers
    assert "DOE" not in json.dumps(meta)
