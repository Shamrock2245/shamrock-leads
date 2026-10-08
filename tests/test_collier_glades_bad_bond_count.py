"""Read-only Collier / Glades bad-bond counter (cleanup prep, NOT RUN).

No network, no Mongo. Checks that the counter prints counts only, never
touches staff-provenance rows or rows written after the fix, and that its
affected-rows filter (used by the plan's mongoexport / update) selects
exactly the rows the summary calls bad.
"""
from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cg", ROOT / "scripts" / "collier_glades_bad_bond_count.py")
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)

BEFORE = "2026-10-09T00:00:00"
_MISSING = object()


def _type_ok(val, t):
    return t == "date" and isinstance(val, datetime)


def _field(val, cond):
    if isinstance(cond, dict) and cond and all(k.startswith("$") for k in cond):
        for op, arg in cond.items():
            if op == "$options":
                continue
            if op == "$regex":
                if not isinstance(val, str) or not re.search(arg, val, re.I if "i" in cond.get("$options", "") else 0):
                    return False
            elif op == "$in":
                if (None if val is _MISSING else val) not in arg:
                    return False
            elif op == "$nin":
                if (None if val is _MISSING else val) in arg:
                    return False
            elif op == "$exists":
                if (val is not _MISSING) != arg:
                    return False
            elif op == "$type":
                if not _type_ok(val, arg):
                    return False
            elif op == "$lt":
                if isinstance(arg, datetime):
                    if not isinstance(val, datetime) or not val < arg:
                        return False
                elif not isinstance(val, str) or not val < arg:
                    return False
            else:
                raise AssertionError(op)
        return True
    if cond is None:
        return val is _MISSING or val is None
    return val == cond


def matches(doc, q):
    for k, c in q.items():
        if k == "$and":
            if not all(matches(doc, s) for s in c):
                return False
        elif k == "$or":
            if not any(matches(doc, s) for s in c):
                return False
        elif k == "$nor":
            if any(matches(doc, s) for s in c):
                return False
        elif not _field(doc.get(k, _MISSING), c):
            return False
    return True


def _doc(county, raw, amount, scraped="2026-10-01T12:00:00+00:00", **extra):
    d = {"county": county, "state": "FL", "bond_amount_raw": raw, "bond_amount": amount,
         "scraped_at": scraped, "full_name": "DOE, JANE", "booking_number": "202600000001"}
    d.update(extra)
    return d


COLLIER = [
    _doc("Collier", "0", 0.0),                                   # old unknown default
    _doc("Collier", "750.0", 750.0),                             # from "$750-$5K" charge text
    _doc("Collier County", "2250.0", 2250.0, state=None),        # legacy suffix, no state
    _doc("Collier", "", 0.0),                                    # already unknown
    _doc("Collier", "", 0.0, scraped="2026-10-09T03:00:00+00:00"),
    _doc("Collier", "5000", 5000.0, scraped="2026-10-09T03:00:00+00:00"),  # written after the fix
    _doc("Collier", "1500", 1500.0, staff_edits={"bond": {"amount": 1500.0}}),
    _doc("Collier", "900", 900.0, bond_override=True),
    _doc("Collier", "100.0", 100.0, scraped=datetime(2026, 9, 1)),  # legacy date-typed scraped_at
    _doc("Collier", "1", 1.0, state="SC"),                       # not FL
]
GLADES = [
    _doc("Glades", "0", 0.0),
    _doc("Glades", "0.00", 0.0),
    _doc("Glades", "65000.00", 65000.0),                         # cannot trace own vs next card
    _doc("Glades", "", 0.0),
    _doc("Glades", "0", 0.0, last_checked_mode="MANUAL_CHARGE_BONDS"),
    _doc("Glades", "0", 0.0, scraped="2026-10-09T05:00:00+00:00"),  # fixed parser: a real printed 0
]


def _fl(docs, county):
    return [d for d in docs if matches(d, cg.county_clause(county))]


def test_collier_summary_counts_every_stored_bond_as_bad():
    out = cg.summarize("Collier", _fl(COLLIER, "Collier"), BEFORE)
    assert out["rows"] == 9
    assert out["skipped_staff_provenance"] == 2
    assert out["last_scraped_after_fix"] == 2
    assert out["bad_zero"] == 1
    assert out["bad_positive_from_charge_text"] == 3
    assert out["would_blank"] == 4


def test_glades_summary_targets_only_zero_and_reports_unattributable():
    out = cg.summarize("Glades", _fl(GLADES, "Glades"), BEFORE)
    assert out["bad_zero"] == 2
    assert out["positive_unattributable"] == 1
    assert out["would_blank"] == 2
    assert "cannot be traced" in out["note"]


@pytest.mark.parametrize("county,docs", [("Collier", COLLIER), ("Glades", GLADES)])
def test_filter_selects_exactly_the_rows_the_summary_would_blank(county, docs):
    q = cg.affected_filter(county, BEFORE)
    hit = [d for d in docs if matches(d, q)]
    assert len(hit) == cg.summarize(county, _fl(docs, county), BEFORE)["would_blank"]
    for d in hit:
        assert not d.get("staff_edits") and d.get("bond_override") is not True
        assert d.get("last_checked_mode") != "MANUAL_CHARGE_BONDS"


def test_output_has_counts_only_no_pii():
    blob = json.dumps({c: cg.summarize(c, _fl(d, c), BEFORE) for c, d in (("Collier", COLLIER), ("Glades", GLADES))})
    for secret in ("DOE", "JANE", "202600000001"):
        assert secret not in blob
    assert set(cg.PROJECTION) >= {"_id"} and cg.PROJECTION["_id"] == 0
    assert not {"full_name", "booking_number", "dob", "address"} & set(cg.PROJECTION)


def test_script_is_read_only_and_needs_a_uri(capsys, monkeypatch):
    src = (ROOT / "scripts" / "collier_glades_bad_bond_count.py").read_text()
    for banned in ("update_one", "update_many", "delete_", "insert_", "replace_one", "bulk_write", "drop("):
        assert banned not in src, banned
    monkeypatch.delenv("MONGODB_URI", raising=False)
    assert cg.main(["--before", BEFORE]) == 1
    assert "MONGODB_URI is not set" in capsys.readouterr().out
    assert cg.main(["--before", BEFORE, "--print-filter", "Glades"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert {"$date": BEFORE + "Z"} in [c.get("scraped_at", {}).get("$lt") for c in printed["$and"][2]["$or"]]


def test_plan_doc_is_marked_not_run_and_has_backup_and_rollback():
    text = (ROOT / "docs" / "ops" / "COLLIER_GLADES_BOND_CLEANUP_PLAN.md").read_text()
    assert text.splitlines()[0].startswith("# ") and "NOT RUN" in text.split("\n\n")[1]
    for needle in ("mongoexport", "bond_cleanup_2026_10", "staff_edits", "Rollback", "Verification",
                   "scripts/collier_glades_bad_bond_count.py"):
        assert needle in text, needle
