"""CLEAR_UNPUBLISHED_STATUS: scoped clear of stored, unsourced "In Custody" (Codex P1 on #281).

Off by default and enabled nowhere. Enabling a county changes stored prod rows
and needs a Leads Ops count plus the owner's OK (tracked in #264). All data is synthetic.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import writers.mongo_writer as mw
from core.models import ArrestRecord
from tests.test_staff_edits_survive_rescrape import FakeArrests, _writer

ROOT = Path(__file__).resolve().parent.parent


def _rec(booking, status="", status_source=""):
    r = ArrestRecord(Booking_Number=booking, County="Volusia", State="FL", Full_Name="RECORD, TEST", Status=status)
    if status_source:
        r.extra_data = {"status_source": status_source}
    return r


def _stored():
    return [
        # invented by the old fallback: no status_source
        {"state": "FL", "county": "Volusia", "booking_number": "V1", "status": "In Custody", "extra": {}},
        # published custody, recorded source
        {"state": "FL", "county": "Volusia", "booking_number": "V2", "status": "In Custody",
         "extra": {"status_source": "published_in_custody_column"}},
        # Released (e.g. a recheck), no source: never cleared
        {"state": "FL", "county": "Volusia", "booking_number": "V3", "status": "Released"},
        # same booking number in another state: never touched
        {"state": "GA", "county": "Volusia", "booking_number": "V1", "status": "In Custody"},
    ]


def _run(monkeypatch, scopes, records):
    monkeypatch.setattr(mw.settings, "CLEAR_UNPUBLISHED_STATUS", frozenset(scopes), raising=False)
    arrests = FakeArrests(_stored())
    _writer(arrests).write_records(records, "Volusia")
    by_key = {(d["state"], d["booking_number"]): d for d in arrests.docs}
    return by_key


def _blank_all():
    return [_rec("V1"), _rec("V2"), _rec("V3")]


def test_default_is_off():
    env = {k: v for k, v in os.environ.items() if k != "CLEAR_UNPUBLISHED_STATUS"}
    out = subprocess.run(
        [sys.executable, "-c", "from config.settings import settings as s; print(repr(s.CLEAR_UNPUBLISHED_STATUS))"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()[-1]
    assert out == "frozenset()"
    assert not mw.clear_unpublished_status_enabled("FL", "Volusia")


def test_flag_off_leaves_stored_status_unchanged(monkeypatch):
    docs = _run(monkeypatch, [], _blank_all())
    assert docs[("FL", "V1")]["status"] == "In Custody"
    assert docs[("FL", "V2")]["status"] == "In Custody"
    assert docs[("FL", "V3")]["status"] == "Released"


def test_other_county_listed_is_still_off_for_volusia(monkeypatch):
    docs = _run(monkeypatch, ["FL/POLK", "GA/VOLUSIA"], _blank_all())
    assert docs[("FL", "V1")]["status"] == "In Custody"


def test_flag_on_clears_only_unsourced_in_custody(monkeypatch):
    docs = _run(monkeypatch, ["FL/VOLUSIA"], _blank_all())
    assert docs[("FL", "V1")]["status"] == ""            # invented, cleared
    assert docs[("FL", "V2")]["status"] == "In Custody"  # had a recorded status_source
    assert docs[("FL", "V3")]["status"] == "Released"    # not custody
    assert docs[("GA", "V1")]["status"] == "In Custody"  # other state


def test_flag_on_published_status_still_wins(monkeypatch):
    docs = _run(monkeypatch, ["FL/VOLUSIA"], [_rec("V1", "Released", "published_in_custody_column")])
    assert docs[("FL", "V1")]["status"] == "Released"
    assert docs[("FL", "V1")]["extra"]["status_source"] == "published_in_custody_column"


def test_flag_on_never_creates_rows(monkeypatch):
    monkeypatch.setattr(mw.settings, "CLEAR_UNPUBLISHED_STATUS", frozenset({"FL/VOLUSIA"}), raising=False)
    op = mw.unpublished_status_clear_op("FL", "Volusia", "NEW")
    assert op._upsert is False
    assert op._filter["status"] == "In Custody" and op._filter["extra.status_source"] == {"$exists": False}


def test_flag_not_enabled_in_deploy_compose_or_env_example():
    for rel in (".env.example", "docker-compose.yml", ".github/workflows/deploy-hetzner.yml"):
        assert "CLEAR_UNPUBLISHED_STATUS" not in (ROOT / rel).read_text(encoding="utf-8"), rel
