"""Charlotte FL and Manatee FL are fail_closed (2026-10-09).

The Revize rosters answer a Cloudflare challenge on page 1 from the agent box,
T-Mobile AS21928 and Comcast AS7922 (stock Chromium, honest UA). No other
official plain-HTTP source publishes a booking roster with a source booking
number (docs/recon/FL_CHARLOTTE_SOURCE_RECON_2026-10-09.md,
docs/recon/FL_MANATEE_SOURCE_RECON_2026-10-09.md). The parser stays for an
easy reopen; the relay never attempts a fail_closed county.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from config.relay_only import RELAY_ONLY_LABELS
from scrapers.counties import charlotte, manatee
from scrapers.counties.charlotte import CharlotteCountyScraper
from scrapers.counties.manatee import ManateeCountyScraper

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    (CharlotteCountyScraper, charlotte, "Charlotte", "015", "FL_CHARLOTTE_SOURCE_RECON_2026-10-09.md",
     "charlotte_residential_smoke.py"),
    (ManateeCountyScraper, manatee, "Manatee", "081", "FL_MANATEE_SOURCE_RECON_2026-10-09.md",
     "manatee_residential_smoke.py"),
]


def _explode(name):
    def _boom(*a, **k):
        raise AssertionError(f"{name} must not be called for a fail_closed county")
    return _boom


@pytest.mark.parametrize("cls, mod, county, fips, doc, smoke", CASES)
def test_fail_closed_with_documented_reason(cls, mod, county, fips, doc, smoke):
    assert cls.SOURCE_CONTRACT_VALIDATED is False
    assert "Cloudflare challenge" in cls.SOURCE_CONTRACT_REASON
    assert (ROOT / "docs" / "recon" / doc).exists()


@pytest.mark.parametrize("cls, mod, county, fips, doc, smoke", CASES)
def test_scrape_makes_no_egress_check_browser_or_request(monkeypatch, cls, mod, county, fips, doc, smoke):
    monkeypatch.setattr(mod, "resolve_egress", _explode("resolve_egress"))
    monkeypatch.setattr(mod, "launch_plain_browser", _explode("launch_plain_browser"))
    assert cls().scrape() == []


@pytest.mark.parametrize("cls, mod, county, fips, doc, smoke", CASES)
def test_run_stops_at_the_source_contract_guard(monkeypatch, cls, mod, county, fips, doc, smoke):
    import scrapers.base_scraper as bs

    monkeypatch.setattr(bs, "_dashboard_available", False)
    monkeypatch.setattr(cls, "scrape", _explode("scrape"))
    out = cls().run(writers=[], force_canary=True)
    assert out["source_contract_state"] == "fail_closed" and out["records_scraped"] == 0


@pytest.mark.parametrize("cls, mod, county, fips, doc, smoke", CASES)
def test_health_and_matrix_are_fail_closed(cls, mod, county, fips, doc, smoke):
    from dashboard.extensions import SCRAPER_SOURCE_STATES

    assert SCRAPER_SOURCE_STATES[f"{county} (FL)"] == "fail_closed"
    rows = json.loads((ROOT / "docs/recon/county_source_contract_evidence.json").read_text())
    rows = rows if isinstance(rows, list) else next(v for v in rows.values() if isinstance(v, list))
    row = next(r for r in rows if r["state"] == "FL" and r["county_fips"] == fips)
    assert row["passive_recommendation"] == "fail_closed"
    assert doc in row["evidence_note"]
    # Still relay-only: the VPS scheduler never gives it an interval job either.
    assert f"{county} (FL)" in RELAY_ONLY_LABELS


@pytest.mark.parametrize("cls, mod, county, fips, doc, smoke", CASES)
def test_relay_smoke_exits_4_without_touching_the_source(monkeypatch, capsys, cls, mod, county, fips, doc,
                                                       smoke):
    monkeypatch.setattr(mod, "resolve_egress", _explode("resolve_egress"))
    monkeypatch.setattr(mod, "launch_plain_browser", _explode("launch_plain_browser"))
    spec = importlib.util.spec_from_file_location(smoke[:-3], ROOT / "scripts" / smoke)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main() == 4
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["result"] == "fail_closed"


class _FakeRelay:
    def __init__(self, county, validated):
        self.county, self.state = county, "FL"
        self.scraper_id = f"scraper_{county.lower()}"
        self.county_label = f"{county} (FL)"
        self.SOURCE_CONTRACT_VALIDATED = validated
        self.SOURCE_CONTRACT_REASON = "challenge"
        self.runs = 0

    def health_check(self):
        return {}

    def run(self, writers=None, force_canary=False):
        self.runs += 1
        return {"county": self.county, "records_scraped": 1}


def test_relay_run_skips_fail_closed_counties_and_still_runs_pinellas():
    from core.scheduler import ScraperScheduler

    sched = ScraperScheduler(max_workers=1)
    cha, man, pin = _FakeRelay("Charlotte", False), _FakeRelay("Manatee", False), _FakeRelay("Pinellas", True)
    for s in (cha, man, pin):
        sched.register_scraper(s)
    results = sched.run_relay_only()
    assert cha.runs == 0 and man.runs == 0 and pin.runs == 1
    for c in ("Charlotte", "Manatee"):
        assert results[c]["status"] == "fail_closed" and results[c]["skipped"] is True
        # main.py --relay-only counts a result as failed only on a falsy result or "error".
        assert results[c] and not results[c].get("error")


def test_real_scrapers_are_skipped_by_the_relay():
    from core.scheduler import ScraperScheduler

    sched = ScraperScheduler(max_workers=1)
    cha, man = CharlotteCountyScraper(), ManateeCountyScraper()
    cha.run = _explode("Charlotte run")
    man.run = _explode("Manatee run")
    sched.register_scraper(cha)
    sched.register_scraper(man)
    results = sched.run_relay_only()
    assert {r["status"] for r in results.values()} == {"fail_closed"}
