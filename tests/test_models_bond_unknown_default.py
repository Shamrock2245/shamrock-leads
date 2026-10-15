"""ArrestRecord.Bond_Amount defaults to "" (unknown), never an invented "0".

A published amount, including a real $0, is kept everywhere it flows: the
record, the Mongo doc, from_mongo, the sheet row and the scorer. Downstream
readers that used to assume "0" are covered here. Synthetic data only.
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from core.models import ArrestRecord, _bond_raw_from_doc
from scoring.lead_scorer import LeadScorer

ROOT = Path(__file__).resolve().parents[1]


def _rec(**kw):
    base = dict(County="Test", State="FL", Booking_Number="B1", Full_Name="X, Y")
    base.update(kw)
    return ArrestRecord(**base)


def test_default_is_unknown():
    r = _rec()
    assert r.Bond_Amount == ""
    doc = r.to_mongo_doc()
    assert doc["bond_amount_raw"] == ""
    assert doc["bond_amount"] == 0.0  # numeric field unchanged; raw carries unknown


@pytest.mark.parametrize("val,raw", [("0", "0"), (0, "0"), (0.0, "0.0"), ("0.00", "0.00"), ("$0.00", "$0.00")])
def test_published_zero_is_kept(val, raw):
    r = _rec(Bond_Amount=val)
    assert r.Bond_Amount == raw
    assert r.to_mongo_doc()["bond_amount_raw"] == raw
    back = ArrestRecord.from_mongo(r.to_mongo_doc()) if hasattr(ArrestRecord, "from_mongo") else None
    if back is not None:
        assert back.Bond_Amount == raw


@pytest.mark.parametrize("doc,expected", [
    ({"bond_amount_raw": "0", "bond_amount": 0.0}, "0"),          # stored published 0 kept
    ({"bond_amount_raw": "", "bond_amount": 0.0}, ""),            # stored unknown kept
    ({"bond_amount_raw": "1500", "bond_amount": 1500.0}, "1500"),
    ({"bond_amount": 2500.0}, "2500.0"),                          # legacy positive numeric only
    ({"bond_amount": 0.0}, ""),                                   # legacy numeric 0: unproven -> unknown
    ({}, ""),
    ({"bond_amount_raw": None, "bond_amount": None}, ""),
])
def test_bond_raw_from_doc(doc, expected):
    assert _bond_raw_from_doc(doc) == expected


def test_sheet_row_carries_unknown_and_zero():
    hdr = ArrestRecord.get_header_row()
    i = hdr.index("Bond_Amount")
    assert _rec().to_sheet_row()[i] == ""
    assert _rec(Bond_Amount="0").to_sheet_row()[i] == "0"


def test_scorer_penalises_published_zero_but_not_unknown():
    s = LeadScorer()
    assert s._score_bond_amount("") == (0, "")
    pts, reason = s._score_bond_amount("0")
    assert pts < 0 and "$0" in reason


def test_rearrest_notification_marks_unknown_bond(monkeypatch):
    import writers.rearrest_checker as rc

    src = Path(rc.__file__).read_text()
    assert '"bond_known": bond_known' in src and '"bond_amount_raw": bond_amount_raw' in src


def test_staff_rescore_has_no_invented_zero():
    src = (ROOT / "core/staff_edits.py").read_text()
    assert 'if prov.bond_amount else "0"' not in src


def test_sanity_cap_log_has_no_person_name(caplog):
    r = _rec(Full_Name="SECRETNAME, PERSON", Bond_Amount="99999999")
    with caplog.at_level("WARNING"):
        assert r.to_mongo_doc()["bond_amount"] == 0.0
    assert "SECRETNAME" not in caplog.text


def _inheriting_calls():
    files = subprocess.check_output(["git", "ls-files", "scrapers/*.py", "scrapers/**/*.py"], cwd=ROOT).decode().split()
    out = []
    for f in files:
        tree = ast.parse((ROOT / f).read_text())
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) == "ArrestRecord":
                kws = {k.arg for k in n.keywords}
                if "Bond_Amount" not in kws and None not in kws:
                    out.append(f)
    return sorted(set(out))


def test_inheriting_scrapers_now_store_unknown():
    # Every scraper that never sets a bond now inherits "" instead of "0".
    # The list is in the PR body; this just proves the set is non-empty and
    # that the model default is what they get.
    files = _inheriting_calls()
    assert "scrapers/counties/st_lucie.py" in files or files
    assert ArrestRecord.__dataclass_fields__["Bond_Amount"].default == ""
