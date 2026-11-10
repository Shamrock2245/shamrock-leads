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
    # Hendry: MyOCV feed has only a person-level MNI id, no booking number (#143).
    # Charlotte / Manatee: Revize roster challenged from every exit (2026-10-09).
    # DeSoto: only key is the site's internal bid; CoS fail_closed 2026-10-15 (#222).
    for county in ("Hendry", "Charlotte", "Manatee", "DeSoto"):
        assert _matrix_status(text, county) == "fail_closed", county
    # 2026-10-08: main also moved Orange (#134) and Indian River (#136) to candidate_productive;
    # #143 moved Hendry from recon_only to fail_closed; 2026-10-09 Charlotte and Manatee
    # recon_only -> fail_closed; Miami-Dade was fail_closed 2026-10-09 (#166) and reopened
    # recon_only the same day (owner exception).
    # 2026-10-14 Hillsborough and Marion recon_only -> fail_closed (rule conflicts).
    # 2026-10-21 Duval recon_only -> fail_closed (tls_cert_invalid; FL_DUVAL_HONEST.md).
    # 2026-10-23 Citrus candidate_productive -> recon_only (honest probe HTTP 403, #217).
    assert "| FL | 67 | 67 | 4 | 19 | 19 | 0 | 25 |" in text


def test_no_home_county_is_promoted_in_health():
    from dashboard.extensions import SCRAPER_SOURCE_STATES

    for county in HOME:
        state = SCRAPER_SOURCE_STATES.get(f"{county} (FL)", "unverified")
        assert state != "verified_public", county
        # Hendry is held fail_closed (#143: person-level MNI id, no booking number);
        # Charlotte / Manatee since 2026-10-09 (Revize roster challenged from every exit).
        # DeSoto since 2026-10-15 (only key is the site's internal bid; CoS option 1, #222).
        held = ("Hendry", "Charlotte", "Manatee", "DeSoto")
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
    assert ("Charlotte (FL)", "relay_read") in by and ("Charlotte (FL)", "relay_write") in by
    # Manatee (FL) jail is fail_closed (2026-10-09): its relay read/write
    # requests were withdrawn 2026-10-12 as moot.
    assert not any(r["label"] == "Manatee (FL)" for r in rows)
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


def test_smoke_evidence_check_reports_manatee_clerk_internal_key_fields():
    """The Manatee Clerk write-smoke `expect` is checkable from the aggregate alone.

    Internal key shape and validity, the internal flag, case / OBTS presence and
    booking == case number are counted; no key, case or OBTS value is printed.
    """
    from scrapers.counties.manatee_clerk import mc_case_key

    spec = importlib.util.spec_from_file_location("sec", ROOT / "scripts" / "smoke_evidence_check.py")
    sec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sec)
    # Synthetic case numbers (uniform FL case-number shape), not real filings.
    k1, k2 = mc_case_key("2026CF009901AX"), mc_case_key("2026-MM-004402-AX")
    docs = [
        {"booking_number": k1, "booking_key_internal": True, "case_number": "2026CF009901AX",
         "obts_number": "5800012345", "charges": "BATTERY", "bond_amount_raw": ""},
        {"booking_number": k2, "booking_key_internal": True, "case_number": "2026-MM-004402-AX",
         "charges": "TRESPASS", "bond_amount_raw": "0.00"},
        # A regression the smoke must surface: the case number stored as the booking key.
        {"booking_number": "2026-CF-009903-AX", "case_number": "2026CF009903AX", "charges": "DUI"},
        # A malformed internal key is not counted as valid.
        {"booking_number": "mc_case_v1:abc", "booking_key_internal": True, "case_number": "2026CF1AX"},
    ]
    out = sec.summarize(docs)
    assert out["rows"] == 4
    assert out["booking_number_blank"] == 0
    assert out["booking_key_internal"] == 3
    assert out["internal_key_valid"] == 2
    assert out["with_case_number"] == 4
    assert out["with_obts_number"] == 1
    assert out["booking_equals_case_number"] == 1
    assert out["booking_number_shapes"]["mc_case_v1:<sha256>"] == 2
    assert out["with_charges"] == 3
    blob = json.dumps(out)
    for secret in (k1, k2, k1.split(":")[1][:16], "2026CF009901AX", "009903", "5800012345", "abc"):
        assert secret not in blob
    # Ordinary booking numbers are unchanged.
    assert sec.key_shape("1033660") == "NNNNNNN"
    assert sec.internal_key_family("1033660") == ""
    assert "case_number" in sec.PROJECTION and "obts_number" in sec.PROJECTION
    assert "full_name" not in sec.PROJECTION


def test_manatee_clerk_smoke_row_names_the_checkable_fields():
    rows = [r for r in json.loads(SMOKE.read_text())["records"] if r["label"] == "Manatee Clerk (FL)"]
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "write_smoke" and row["status"] == "requested"
    assert '--county "Manatee Clerk" --state FL' in row["command"]
    for field in ("booking_key_internal", "internal_key_valid", "with_case_number",
                  "booking_equals_case_number", "with_charges"):
        assert field in row["expect"], field
    assert "merged" in row["blocked_on"].lower() and "deploy" in row["blocked_on"].lower()


def test_smoke_evidence_check_internal_key_needs_a_full_match():
    """Codex 11/3 on #308: a key with a trailing newline is not a valid internal key.

    ``re.match`` with ``$`` accepts ``key + "\\n"``; the writer's
    ``internal_natural_key()`` uses ``fullmatch`` and rejects it, so the smoke must too.
    """
    from scrapers.counties.manatee_clerk import mc_case_key

    spec = importlib.util.spec_from_file_location("sec", ROOT / "scripts" / "smoke_evidence_check.py")
    sec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sec)
    key = mc_case_key("2026CF009901AX")
    assert sec.internal_key_family(key) == "mc_case_v1"
    for bad in (key + "\n", key + " ", " " + key, key.upper()):
        assert sec.internal_key_family(bad) == "", repr(bad[-2:])
        assert sec.key_shape(bad) != "mc_case_v1:<sha256>"
    out = sec.summarize([{"booking_number": key + "\n", "booking_key_internal": True}])
    assert out["internal_key_valid"] == 0
    assert "mc_case_v1:<sha256>" not in out["booking_number_shapes"]


# --- Codex 11/4 on #308: Manatee Clerk identity counters gate a passed smoke ---


def _sec():
    spec = importlib.util.spec_from_file_location("sec_1104", ROOT / "scripts" / "smoke_evidence_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mc_counters(**over):
    res = {"status": "ok", "rows": 12, "window_hours": 24, "booking_number_blank": 0,
           "booking_number_duplicates": 0, "booking_number_shapes": {"mc_case_v1:<sha256>": 12},
           "booking_key_internal": 12, "internal_key_valid": 12, "internal_key_matches_case": 12,
           "bond": {"empty": 12},
           "with_case_number": 12,
           "with_obts_number": 9, "booking_equals_case_number": 0, "with_charges": 12,
           "with_booking_date": 0, "status_buckets": {"": 12}}
    res.update(over)
    return res


def _mc_passed(method="prod_mongo_aggregate", **over):
    res = _mc_counters(**over)
    if method == "one_shot_run":
        res.update({"new_records": 12, "updated_records": 0, "mongo_writer_results": 1})
    return {**BASE, "label": "Manatee Clerk (FL)", "kind": "write_smoke", "status": "passed",
            "run_on": "2026-10-09", "commit": "abc1234", "egress": "vps", "method": method, "result": res}


@pytest.mark.parametrize("method", ["prod_mongo_aggregate", "one_shot_run"])
def test_codex_1104_manatee_clerk_good_counters_pass(tmp_path, method):
    text, _ = _build(_write(tmp_path, [_mc_passed(method)]))
    assert "| Manatee Clerk (FL) |" in text and "| passed |" in text


@pytest.mark.parametrize("method", ["prod_mongo_aggregate", "one_shot_run"])
@pytest.mark.parametrize("over", [
    # The exact case Codex named.
    {"booking_key_internal": 0, "internal_key_valid": 0, "with_case_number": 0, "booking_equals_case_number": 1},
    {"booking_key_internal": 11}, {"internal_key_valid": 11}, {"with_case_number": 11},
    {"booking_equals_case_number": 1}, {"with_charges": 11}, {"with_booking_date": 1},
    {"status_buckets": {"": 11, "IN CUSTODY": 1}}, {"booking_key_internal": None},
    {"internal_key_valid": "12"}, {"booking_key_internal": True},
])
def test_codex_1104_manatee_clerk_bad_counters_fail_closed(tmp_path, method, over):
    with pytest.raises(RuntimeError, match="identity gate failed for Manatee Clerk"):
        _build(_write(tmp_path, [_mc_passed(method, **over)]))


@pytest.mark.parametrize("key", ["booking_key_internal", "internal_key_valid", "internal_key_matches_case",
                                 "with_case_number",
                                 "booking_equals_case_number", "with_charges", "with_booking_date",
                                 "status_buckets", "booking_number_shapes"])
def test_codex_1104_manatee_clerk_missing_counter_fails(tmp_path, key):
    rec = _mc_passed()
    del rec["result"][key]
    with pytest.raises(RuntimeError, match="identity gate failed"):
        _build(_write(tmp_path, [rec]))


def test_codex_1104_other_sources_have_no_identity_gate(tmp_path):
    """Guard: Lee's documented aggregate still passes without Manatee counters."""
    assert _sec().identity_gate("Lee (FL)", {"rows": 1}) is None
    text, _ = _build(_write(tmp_path, [_prod_aggregate()]))
    assert "| Lee (FL) |" in text


def test_codex_1104_script_exits_nonzero_on_a_failed_gate(monkeypatch, capsys):
    """The check itself fails (exit 3, result identity_failed) on bad counters; no PII printed."""
    sec = _sec()
    from scrapers.counties.manatee_clerk import mc_case_key

    good = mc_case_key("2026CF009901AX")
    docs = {"bad": [{"booking_number": "2026CF009901AX", "case_number": "2026CF009901AX", "charges": "X"}],
            "good": [{"booking_number": good, "booking_key_internal": True, "case_number": "2026CF009901AX",
                      "charges": "X", "status": ""}]}

    class _Col:
        def __init__(self, rows):
            self.rows = rows

        def find(self, *a, **k):
            return list(self.rows)

    class _Client:
        def __init__(self, rows):
            self.rows = rows

        def get_database(self, _name):
            return {"arrests": _Col(self.rows)}

    import pymongo
    for which, code, result in (("bad", 3, "identity_failed"), ("good", 0, "ok")):
        monkeypatch.setenv("MONGODB_URI", "mongodb://test-only.invalid")
        monkeypatch.setattr(pymongo, "MongoClient", lambda *a, _r=docs[which], **k: _Client(_r))
        assert sec.main(["--county", "Manatee Clerk", "--state", "FL"]) == code
        out = json.loads(capsys.readouterr().out)
        assert out["result"] == result
        assert out["identity_gate"] == ("fail" if code else "pass")
        assert "2026CF009901AX" not in json.dumps(out)


def test_codex_1104_manatee_clerk_row_documents_the_gate():
    row = next(r for r in json.loads(SMOKE.read_text())["records"] if r["label"] == "Manatee Clerk (FL)")
    assert row["status"] == "requested"
    assert "identity_gate pass" in row["expect"]



# --- Codex 11/5 on #308: Manatee family-only keys + blank status_buckets count ---


@pytest.mark.parametrize("method", ["prod_mongo_aggregate", "one_shot_run"])
@pytest.mark.parametrize("over", [
    # Codex's exact case: every row is a valid Miami-Dade key.
    {"booking_number_shapes": {"md_dedupe_v2:<sha256>": 12}, "internal_key_valid": 12},
    {"booking_number_shapes": {"mc_case_v1:<sha256>": 11, "md_dedupe_v2:<sha256>": 1}},
    {"booking_number_shapes": {"mc_case_v1:<sha256>": 11}},
    {"booking_number_shapes": {"NNNNNNN": 12}},
    {"booking_number_shapes": {}},
    {"booking_number_shapes": {"mc_case_v1:<sha256>": "12"}},
])
def test_codex_1105_manatee_clerk_rejects_wrong_key_family(tmp_path, method, over):
    with pytest.raises(RuntimeError, match="identity gate failed for Manatee Clerk"):
        _build(_write(tmp_path, [_mc_passed(method, **over)]))


@pytest.mark.parametrize("method", ["prod_mongo_aggregate", "one_shot_run"])
@pytest.mark.parametrize("buckets", [
    {},
    {"": 0},
    {"": "12"},
    {"": 11},
    {"": True},
    {"IN CUSTODY": 12},
    {"": 12, "OPEN": 0},
])
def test_codex_1105_manatee_clerk_requires_blank_status_count(tmp_path, method, buckets):
    """Codex 11/5: status_buckets must be exactly {"": rows} with an integer count."""
    with pytest.raises(RuntimeError, match="identity gate failed for Manatee Clerk"):
        _build(_write(tmp_path, [_mc_passed(method, status_buckets=buckets)]))


def test_codex_1105_manatee_clerk_gate_helpers_name_the_problems():
    sec = _sec()
    bad_family = _mc_counters(booking_number_shapes={"md_dedupe_v2:<sha256>": 12})
    probs = sec.manatee_clerk_identity_problems(bad_family)
    assert any("booking_number_shapes" in p for p in probs)
    bad_status = _mc_counters(status_buckets={})
    probs = sec.manatee_clerk_identity_problems(bad_status)
    assert any("status_buckets" in p for p in probs)
    assert sec.manatee_clerk_identity_problems(_mc_counters()) == []


def test_codex_1110_manatee_clerk_gate_ignores_label_case_and_space():
    """Codex 11/10 on #308: CLI spelling must not skip the identity gate.

    ``--county "manatee clerk"`` still selects the rows, so the gate has to
    run. The counters themselves stay exact.
    """
    sec = _sec()
    bad = _mc_counters(booking_number_shapes={"md_dedupe_v2:<sha256>": 12})
    labels = (
        "manatee clerk (FL)",
        "  MANATEE   CLERK (fl)",
        "Manatee Clerk (FL)",
        " manatee clerk  (FL)",
        "Manatee Clerk ( FL )",
    )
    for label in labels:
        problems = sec.identity_gate(label, bad)
        assert problems is not None and any("booking_number_shapes" in p for p in problems), label
        blank = sec.identity_gate(label, _mc_counters(status_buckets={}))
        assert blank is not None and any("status_buckets" in p for p in blank), label
        assert sec.identity_gate(label, _mc_counters()) == [], label
    assert sec.identity_gate("Lee (FL)", {"rows": 1}) is None


def test_codex_1110_d_florida_alias_and_bond_buckets():
    """Codex 11/10 on 176b381: Florida must hit the gate, and a bad bond must not pass.

    ``--state Florida`` selects Florida rows and used to label the result
    ``Manatee Clerk (FLORIDA)``, which skipped the gate. ``negative`` and
    ``text`` bond buckets are not a Clerk bond.
    """
    sec = _sec()
    assert sec.evidence_label("Manatee Clerk", "Florida") == "Manatee Clerk (FL)"
    assert sec.evidence_label("Manatee Clerk", " florida ") == "Manatee Clerk (FL)"
    assert sec.canonical_state("FLORIDA") == "FL"
    bad_shape = _mc_counters(booking_number_shapes={"md_dedupe_v2:<sha256>": 12})
    for label in ("Manatee Clerk (FLORIDA)", "Manatee Clerk ( Florida )"):
        problems = sec.identity_gate(label, bad_shape)
        assert problems is not None and any("booking_number_shapes" in p for p in problems), label
    query = sec.county_query("Manatee Clerk", "Florida", __import__("datetime").datetime(2026, 11, 10))
    assert "$or" in query["$and"][1]
    for bad_bond in ({"negative": 12}, {"text": 12}, {"empty": 1}, {}):
        found = sec.manatee_clerk_identity_problems(_mc_counters(bond=bad_bond))
        assert any("bond" in problem for problem in found), bad_bond
    assert sec.manatee_clerk_identity_problems(_mc_counters(bond={"empty": 10, "positive": 2})) == []
    assert sec.manatee_clerk_identity_problems(_mc_counters(bond={"zero": 12})) == []


def test_codex_1110_c_falsy_status_is_not_a_blank_bucket():
    """Codex 11/10 on 8a016ba: 0, false, and empty containers are not blank custody.

    Only None and a blank string count as ``""``. Anything else fails the
    Manatee gate, which still requires ``status_buckets == {"": rows}``.
    """
    sec = _sec()
    malformed = sec.summarize([
        {"booking_number": "1", "status": 0},
        {"booking_number": "2", "status": False},
        {"booking_number": "3", "status": []},
        {"booking_number": "4", "status": {}},
    ])
    assert "" not in malformed["status"]
    assert malformed["status"] == {"malformed": 4}
    blank = sec.summarize([
        {"booking_number": "a", "status": None},
        {"booking_number": "b", "status": ""},
        {"booking_number": "c", "status": "   "},
        {"booking_number": "d"},
    ])
    assert blank["status"] == {"": 4}
    assert sec.summarize([{"booking_number": "e", "status": "In Custody"}])["status"] == {"In Custody": 1}
    problems = sec.manatee_clerk_identity_problems(_mc_counters(status_buckets=malformed["status"]))
    assert any("status_buckets" in problem for problem in problems)
    assert sec.manatee_clerk_identity_problems(_mc_counters()) == []


def test_codex_1110_b_key_must_match_its_case_and_state_label_is_stripped():
    """Codex 11/10 on a4b35de: a swapped key and a padded state must not pass.

    A well-shaped ``mc_case_v1`` key from a different case number is not valid
    evidence. ``--state " FL "`` must still select the Manatee Clerk gate.
    Neither the key nor the case number is printed.
    """
    from scrapers.counties.manatee_clerk import mc_case_key

    sec = _sec()
    case_a = "2026CF009901AX"
    case_b = "2026MM004402AX"
    key_a = mc_case_key(case_a)
    key_b = mc_case_key(case_b)
    assert sec.mc_case_digest(case_a) == key_a
    assert sec.mc_case_digest("2026-CF-009901-AX") == key_a

    swapped = {
        "booking_number": key_b,
        "booking_key_internal": True,
        "case_number": case_a,
        "charges": "BATTERY",
        "status": "",
    }
    out = sec.summarize([swapped])
    assert out["internal_key_valid"] == 1
    assert out["internal_key_matches_case"] == 0
    blob = json.dumps(out)
    for secret in (key_a, key_b, case_a, case_b, key_a.split(":")[1]):
        assert secret not in blob

    matched = dict(swapped, booking_number=key_a)
    good = sec.summarize([matched])
    assert good["internal_key_matches_case"] == 1
    assert json.dumps(good).find(case_a) == -1

    counters = _mc_counters()
    assert sec.manatee_clerk_identity_problems(counters) == []
    swapped_counters = _mc_counters(internal_key_matches_case=0)
    problems = sec.manatee_clerk_identity_problems(swapped_counters)
    assert any("internal_key_matches_case" in p for p in problems)
    assert sec.identity_gate("Manatee Clerk ( FL )", swapped_counters)
    assert sec.identity_gate("Manatee Clerk ( FL )", counters) == []
    assert sec.evidence_label("Manatee Clerk", " FL ") == "Manatee Clerk (FL)"
    assert sec.evidence_label("  manatee   clerk ", "fl") == "manatee clerk (FL)"
