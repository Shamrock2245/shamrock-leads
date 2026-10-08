"""Manatee + Charlotte (2026-10-07) and Pinellas (2026-10-08) are relay-only: never on the VPS scheduler."""
from __future__ import annotations

import ast
import inspect

from config.relay_only import RELAY_ONLY_LABELS, is_relay_only


class _Fake:
    def __init__(self, county, state="FL"):
        self.county, self.state = county, state
        self.scraper_id = f"scraper_{county.lower()}"
        self.county_label = f"{county} ({state})"
        self.runs = 0

    def health_check(self):
        return {}

    def run(self, writers=None, force_canary=False):
        self.runs += 1
        return {"county": self.county, "records_scraped": 1}


def _sched():
    from core.scheduler import ScraperScheduler

    return ScraperScheduler(max_workers=1)


def test_relay_only_set_is_manatee_charlotte_and_pinellas_fl():
    # Pinellas joined 2026-10-08 (owner exception, docs/ops/PINELLAS_RELAY_RUN.md).
    assert RELAY_ONLY_LABELS == {"Manatee (FL)", "Charlotte (FL)", "Pinellas (FL)"}
    assert is_relay_only(_Fake("Manatee")) and is_relay_only(_Fake("Charlotte"))
    assert not is_relay_only(_Fake("Charlotte", "NC"))  # Charlotte NC (Mecklenburg) is not this
    assert not is_relay_only(_Fake("Lee"))


def test_relay_only_counties_get_no_interval_job():
    sched = _sched()
    for c in ("Manatee", "Charlotte", "Lee"):
        sched.register_scraper(_Fake(c), interval_minutes=60)
    job_ids = {j.id for j in sched.scheduler.get_jobs()}
    assert job_ids == {"scraper_lee"}
    # Still registered, so `python main.py Manatee` resolves.
    assert {"scraper_manatee", "scraper_charlotte"} <= set(sched._scrapers)
    assert sched.relay_only_job_ids() == ["scraper_charlotte", "scraper_manatee"]
    assert sched.get_status()["relay_only"] == ["Charlotte", "Manatee"]


def test_run_relay_only_runs_each_once_and_keeps_going_after_a_failure():
    sched = _sched()
    man, cha, lee = _Fake("Manatee"), _Fake("Charlotte"), _Fake("Lee")

    def boom(**k):
        raise RuntimeError("egress_block")

    cha.run = boom
    for s in (man, cha, lee):
        sched.register_scraper(s)
    results = sched.run_relay_only()
    assert man.runs == 1 and lee.runs == 0
    assert results["Manatee"]["records_scraped"] == 1
    assert "egress_block" in results["Charlotte"]["error"]


class _Triggers:
    def __init__(self, docs):
        self.docs = docs
        self.sets = []

    def find(self, q):
        return [d for d in self.docs if d.get("status") == "pending"]

    def update_one(self, flt, upd):
        self.sets.append((flt, upd["$set"]))


def test_dashboard_trigger_for_relay_only_county_is_not_run_on_vps(monkeypatch):
    sched = _sched()
    man = _Fake("Manatee")
    sched.register_scraper(man)
    triggers = _Triggers([
        {"_id": 1, "county": "Manatee", "status": "pending"},
        {"_id": 2, "county": "Manatee", "status": "pending", "type": "custody_recheck"},
    ])
    db = {"scraper_triggers": triggers}
    class _Client(dict):
        def __init__(self, *a, **k):
            super().__init__({"x": db})

        def __getitem__(self, name):
            return db

        def close(self):
            pass

    import pymongo
    monkeypatch.setattr(pymongo, "MongoClient", _Client)
    sched._poll_triggers()
    assert man.runs == 0
    assert [s["status"] for _, s in triggers.sets] == ["relay_only", "relay_only"]


def test_main_registers_relay_only_and_has_relay_entry_point():
    import main

    src = inspect.getsource(main)
    assert '"--relay-only"' in src and "run_relay_only()" in src
    tree = ast.parse(inspect.getsource(main.register_scrapers))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "register_scraper":
            arg = node.args[0]
            name = getattr(getattr(arg, "func", None), "id", "")
            if name in ("ManateeCountyScraper", "CharlotteCountyScraper", "PinellasCountyScraper"):
                assert not node.keywords, f"{name} must not carry a VPS interval"
