"""Fail-closed source counties stay out of the default lead views.

Sarasota is ``fail_closed`` (no source booking number on its public roster),
but its old rows still came back in the default ``/api/leads`` list, the
Write Book preset and the bond-ready queue. These tests run the real query
builders against a tiny in-memory evaluator so the behaviour, not the dict
shape, is checked. They are generic: every fail_closed label is hidden, and
nothing is deleted.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

import pytest

from config.write_counties import WRITE_ELIGIBLE_COUNTIES
from dashboard.extensions import SCRAPER_SOURCE_STATES, parse_registered_county
from dashboard.models.leads import LeadsQueryModel
from dashboard.routers import stats as stats_router
from dashboard.services.intel_population import population_stages, resolve_preset
from dashboard.services.lead_subscriptions import shamrock_seed


def _filters():
    # Imported lazily so the endpoint tests also run (and fail) on main,
    # where this module does not exist yet.
    from dashboard.services import source_state_filter

    return source_state_filter

_MISSING = object()


def _field(val, cond) -> bool:
    if isinstance(cond, dict) and cond and all(str(k).startswith("$") for k in cond):
        for op, arg in cond.items():
            if op == "$options":
                continue
            if op == "$regex":
                flags = re.I if "i" in cond.get("$options", "") else 0
                if not isinstance(val, str) or not re.search(arg, val, flags):
                    return False
            elif op == "$in":
                if (None if val is _MISSING else val) not in arg:
                    return False
            elif op == "$exists":
                if (val is not _MISSING) != bool(arg):
                    return False
            elif op in ("$gte", "$gt", "$lt", "$lte"):
                try:
                    ok = {
                        "$gte": val >= arg, "$gt": val > arg,
                        "$lt": val < arg, "$lte": val <= arg,
                    }[op]
                except TypeError:
                    return False
                if not ok:
                    return False
            else:
                raise AssertionError(f"evaluator does not know {op}")
        return True
    if cond is None:
        return val is _MISSING or val is None
    return val == cond


def matches(doc: dict, query: dict) -> bool:
    for key, cond in (query or {}).items():
        if key == "$and":
            if not all(matches(doc, sub) for sub in cond):
                return False
        elif key == "$or":
            if not any(matches(doc, sub) for sub in cond):
                return False
        elif key == "$nor":
            if any(matches(doc, sub) for sub in cond):
                return False
        elif not _field(doc.get(key, _MISSING), cond):
            return False
    return True


def _row(bk, county, state=_MISSING, **extra):
    doc = {
        "booking_number": bk,
        "full_name": f"Person {bk}",
        "county": county,
        "scraped_at": "2026-10-08T07:00:00",
        "lead_score": 60,
        "bond_amount": 5000,
        "status": "In Custody",
    }
    if state is not _MISSING:
        doc["state"] = state
    doc.update(extra)
    return doc


ROWS = [
    _row("LEE-1", "Lee", "FL"),
    _row("LEE-2", "Lee"),  # legacy row with no state = Florida
    _row("COL-1", "Collier", "FL"),
    _row("SAR-1", "Sarasota", "FL"),
    _row("SAR-2", "Sarasota"),
    _row("SAR-3", "Sarasota County", "Florida"),
    _row("SAR-4", "SARASOTA", "fl"),
    _row("ALA-1", "Alachua", "FL"),
    _row("LEESC-1", "Lee", "SC"),  # Lee (SC) is fail_closed; Lee (FL) is not
    _row("LEEGA-1", "Lee", "GA"),
]


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_a, **_k):
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return dict(next(self._it))
        except StopIteration:
            raise StopAsyncIteration


class FakeArrests:
    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]

    def find(self, query, projection=None):
        return _Cursor(r for r in self.rows if matches(r, query))

    async def count_documents(self, query):
        return sum(1 for r in self.rows if matches(r, query))

    async def distinct(self, field):
        return sorted({r.get(field) for r in self.rows if r.get(field)})

    def __getattr__(self, name):
        if name.startswith("delete") or name.startswith("update") or name.startswith("insert"):
            raise AssertionError(f"lead views must not write: {name}")
        raise AttributeError(name)


def _api_leads(monkeypatch, **params):
    arrests = FakeArrests(ROWS)
    monkeypatch.setattr(stats_router, "get_collection", lambda _name: arrests)
    stats_router._COUNTIES_CACHE.update({"ts": 0.0, "value": None})
    stats_router._ACTIVITY_CACHE.update({"ts": 0.0, "value": None})
    out = asyncio.run(stats_router.api_leads(LeadsQueryModel(**params)))
    assert "error" not in out, out
    return out, arrests


def _bookings(out):
    return {row["booking_number"] for row in out["leads"]}


def test_sarasota_is_fail_closed_on_this_branch():
    assert SCRAPER_SOURCE_STATES["Sarasota (FL)"] == "fail_closed"
    assert "Sarasota (FL)" in _filters().fail_closed_labels()


def test_default_lead_list_hides_fail_closed_counties(monkeypatch):
    out, arrests = _api_leads(monkeypatch)
    got = _bookings(out)
    assert {"SAR-1", "SAR-2", "SAR-3", "SAR-4", "ALA-1", "LEESC-1"}.isdisjoint(got), got
    assert {"LEE-1", "LEE-2", "COL-1", "LEEGA-1"} <= got
    assert out["total"] == 4
    # Nothing was deleted: the rows are still in the collection.
    assert len(arrests.rows) == len(ROWS)


def test_naming_the_county_or_the_flag_still_shows_them(monkeypatch):
    out, _ = _api_leads(monkeypatch, county="Sarasota (FL)")
    assert _bookings(out) == {"SAR-1", "SAR-2", "SAR-3", "SAR-4"}

    out, _ = _api_leads(monkeypatch, include_fail_closed=True)
    assert _bookings(out) == {r["booking_number"] for r in ROWS}
    assert out["include_fail_closed"] is True

    # Naming Sarasota does not reopen the other fail_closed counties.
    out, _ = _api_leads(monkeypatch, county="Sarasota (FL),Lee (FL)")
    assert "ALA-1" not in _bookings(out)
    assert "LEESC-1" not in _bookings(out)
    assert {"SAR-1", "LEE-1"} <= _bookings(out)


def test_search_does_not_leak_fail_closed_rows(monkeypatch):
    out, _ = _api_leads(monkeypatch, search="Person SAR")
    assert _bookings(out) == set()
    out, _ = _api_leads(monkeypatch, search="Person SAR", include_fail_closed=True)
    assert _bookings(out) == {"SAR-1", "SAR-2", "SAR-3", "SAR-4"}


def test_write_book_preset_drops_fail_closed_but_write_gate_is_unchanged(monkeypatch):
    out, _ = _api_leads(monkeypatch)
    assert "Sarasota" not in out["write_counties"]
    assert "Hardee" not in out["write_counties"]
    assert "Lee" in out["write_counties"]
    assert "Sarasota (FL)" in out["fail_closed_counties"]
    # Where the agency may write paper is a separate rule and is untouched.
    assert "Sarasota" in WRITE_ELIGIBLE_COUNTIES
    assert _filters().default_write_book_counties(["Lee", "Sarasota", "Hardee"]) == ["Lee"]


def test_every_fail_closed_label_is_hidden_not_just_sarasota():
    f = _filters()
    fail_closed_labels, is_fail_closed = f.fail_closed_labels, f.is_fail_closed
    hide = f.fail_closed_exclusion()
    assert hide is not None
    labels = fail_closed_labels()
    assert len(labels) > 50
    for label in labels:
        bare, st = parse_registered_county(label)
        doc = {"county": bare, "state": st}
        assert not matches(doc, hide), label
        assert is_fail_closed(bare, st), label
    for label, state in SCRAPER_SOURCE_STATES.items():
        if state == "fail_closed":
            continue
        bare, st = parse_registered_county(label)
        if f"{bare} ({st})" in labels:
            continue
        assert matches({"county": bare, "state": st}, hide), label


def _population(stages):
    rows = ROWS
    for stage in stages:
        if "$match" in stage:
            rows = [r for r in rows if matches(r, stage["$match"])]
        elif "$addFields" in stage:
            rows = [
                dict(r, _safe_bond=float(r.get("bond_amount") or 0),
                     _custody_str=r.get("status") or "")
                for r in rows
            ]
    return {r["booking_number"] for r in rows}


def test_bond_ready_queue_hides_fail_closed_counties():
    now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    spec = resolve_preset("bond_ready")
    got = _population(population_stages(spec, now=now))
    assert {"LEE-1", "LEE-2", "COL-1", "LEEGA-1"} == got

    got = _population(population_stages(spec, county="Sarasota (FL)", now=now))
    assert got == {"SAR-1", "SAR-2", "SAR-3", "SAR-4"}
    got = _population(population_stages(spec, now=now, include_fail_closed=True))
    assert got == {r["booking_number"] for r in ROWS}


def test_lead_subscription_seed_excludes_fail_closed():
    is_fail_closed = _filters().is_fail_closed
    seeded = {(r["state"], r["county"]) for r in shamrock_seed()}
    assert ("FL", "Sarasota") not in seeded
    assert ("FL", "Lee") in seeded
    for state, county in seeded:
        assert not is_fail_closed(county, state)


@pytest.mark.parametrize("county,state,expected", [
    ("Sarasota", None, True),
    ("Sarasota County", "FL", True),
    ("Sarasota (FL)", None, True),
    ("Lee", "FL", False),
    ("Lee", "SC", True),
    ("St. Johns", "FL", True),
])
def test_is_fail_closed(county, state, expected):
    assert _filters().is_fail_closed(county, state) is expected


# ── Codex review follow-ups ─────────────────────────────────────────────────
def test_naming_a_suffixed_county_label_is_still_an_opt_in():
    # "Sarasota County (FL)" comes from the county-list merger for legacy rows.
    clause = _filters().fail_closed_exclusion(["Sarasota County (FL)"])
    sar = _row("SAR-3", "Sarasota County", "FL")
    assert matches(sar, clause)
    assert not matches(_row("ALA-1", "Alachua", "FL"), clause)
    clause = _filters().fail_closed_exclusion(["Sarasota County"])
    assert matches(_row("SAR-2", "Sarasota"), clause)


def test_presets_load_fail_closed_labels_before_seeding_counties():
    import asyncio
    from pathlib import Path

    out = asyncio.run(stats_router.api_leads_fail_closed_counties())
    assert "Sarasota (FL)" in out["fail_closed_counties"]
    assert set(out["fail_closed_counties"]) == set(_filters().fail_closed_labels())

    paths = [r.path for r in stats_router.router.routes]
    assert paths.index("/api/leads/fail-closed-counties") < paths.index("/api/leads/{booking_number}")

    js = (Path(__file__).resolve().parents[1] / "dashboard" / "sl-core.js").read_text()
    assert "/api/leads/fail-closed-counties" in js
    for fn in ("async function applyPreset(name) {", "async function applyDefCountyPreset(name) {"):
        body = js[js.index(fn):]
        body = body[:body.index("\n}\n")]
        assert body.index("await ensureFailClosedCounties();") < body.index("SL_STATE.")
