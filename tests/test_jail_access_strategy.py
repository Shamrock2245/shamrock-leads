"""Manatee jail and Sarasota jail fetch strategy. Fixtures only. No network.

Owner directive 2026-11-13: Mac relay first, then stealth browser + solver,
then fail closed. Both counties stay SOURCE_CONTRACT_VALIDATED False.
Hillsborough never receives a solver.
"""
from __future__ import annotations

from scrapers.access_strategy import (
    FAIL_CLOSED,
    MAC_RELAY,
    PLAIN,
    RELAY_TOKEN_ENV,
    RELAY_URL_ENV,
    SOLVER_ENV,
    STEALTH_SOLVER,
    plan_for,
    run_access,
)
from scrapers.counties.manatee import ManateeCountyScraper
from scrapers.counties.sarasota import SarasotaCountyScraper
from scrapers.counties.sarasota_contract import assess_listing

ROSTER = "<html><table><tr><th>Booking #</th></tr></table></html>"
LISTING = (
    '<a href="viewInmate.php?id=1000000001">DOE, JANE Q - 01/01/1990</a>'
)


def _env(**extra):
    base = {RELAY_URL_ENV: "", RELAY_TOKEN_ENV: "", SOLVER_ENV: ""}
    base.update(extra)
    return base


def _boom(*_a, **_k):
    raise ConnectionError("down")


def test_strategy_selection_is_only_the_two_jails():
    manatee = plan_for("Manatee", "FL")
    sarasota = plan_for("Sarasota", "fl")
    assert manatee.order == (MAC_RELAY, STEALTH_SOLVER, FAIL_CLOSED)
    assert sarasota.order == manatee.order
    assert manatee.allow_solver and sarasota.allow_solver
    assert ManateeCountyScraper.ACCESS_PLAN is manatee or ManateeCountyScraper.ACCESS_PLAN == manatee
    assert SarasotaCountyScraper.ACCESS_PLAN == sarasota
    lee = plan_for("Lee")
    assert lee.order == (PLAIN,) and lee.allow_solver is False
    hills = plan_for("Hillsborough", "FL")
    assert hills.order == (PLAIN,) and hills.allow_solver is False


def test_solver_missing_fails_closed_without_calling_the_browser():
    called = {"stealth": 0}

    def stealth(*_a, **_k):
        called["stealth"] += 1
        return 200, ROSTER

    result = run_access(
        plan_for("Manatee"),
        env=_env(**{RELAY_URL_ENV: "http://mac.invalid", RELAY_TOKEN_ENV: "tok"}),
        relay_get=_boom,
        stealth_fetch=stealth,
    )
    assert result.ok is False
    assert result.strategy == FAIL_CLOSED
    assert result.body == ""
    assert called["stealth"] == 0
    assert [a.reason for a in result.attempts] == ["relay_unreachable", "solver_missing", "all_paths_failed"]


def test_hillsborough_solver_is_never_called_even_if_a_key_is_set():
    called = {"stealth": 0}

    def stealth(*_a, **_k):
        called["stealth"] += 1
        return 200, ROSTER

    plan = plan_for("Hillsborough")
    # Even a hand-built order cannot turn the solver on for this county.
    from scrapers.access_strategy import AccessPlan
    forced = AccessPlan("Hillsborough", "FL", (STEALTH_SOLVER, FAIL_CLOSED), "https://example.invalid", False)
    assert plan.allow_solver is False
    result = run_access(forced, env=_env(**{SOLVER_ENV: "present"}), relay_get=_boom, stealth_fetch=stealth)
    assert called["stealth"] == 0
    assert result.ok is False
    assert result.attempts[0].reason == "solver_not_allowed"


def test_relay_success_does_not_call_the_solver(monkeypatch):
    monkeypatch.delenv(SOLVER_ENV, raising=False)

    def relay_get(url, token, county, state, target):
        assert token == "tok" and county == "Sarasota" and state == "FL"
        assert target.startswith("https://cms.revize.com/")
        return 200, LISTING

    def stealth(*_a, **_k):
        raise AssertionError("solver must not run after a relay hit")

    result = run_access(
        plan_for("Sarasota"),
        env=_env(**{RELAY_URL_ENV: "http://100.64.0.2:8765", RELAY_TOKEN_ENV: "tok"}),
        relay_get=relay_get,
        stealth_fetch=stealth,
    )
    assert result.ok is True
    assert result.strategy == MAC_RELAY
    assert result.reason == "relay_ok"
    assert len(result.attempts) == 1


def test_relay_unreachable_falls_back_to_stealth():
    def stealth(url, key):
        assert key == "solver-key"
        assert "manatee-sheriff" in url
        return 200, ROSTER

    result = run_access(
        plan_for("Manatee"),
        env=_env(**{
            RELAY_URL_ENV: "http://100.64.0.2:8765",
            RELAY_TOKEN_ENV: "tok",
            SOLVER_ENV: "solver-key",
        }),
        relay_get=_boom,
        stealth_fetch=stealth,
    )
    assert result.ok is True
    assert result.strategy == STEALTH_SOLVER
    assert [a.reason for a in result.attempts] == ["relay_unreachable", "stealth_ok"]


def test_all_paths_failing_stays_fail_closed_and_emits_nothing():
    def stealth(*_a, **_k):
        return 403, "Just a moment"

    result = run_access(
        plan_for("Manatee"),
        env=_env(**{
            RELAY_URL_ENV: "http://100.64.0.2:8765",
            RELAY_TOKEN_ENV: "tok",
            SOLVER_ENV: "solver-key",
        }),
        relay_get=_boom,
        stealth_fetch=stealth,
    )
    assert result.ok is False
    assert result.strategy == FAIL_CLOSED
    assert result.body == ""
    assert result.reason == "all_paths_failed"
    assert [a.reason for a in result.attempts] == [
        "relay_unreachable", "stealth_blocked", "all_paths_failed",
    ]


def test_missing_relay_config_is_not_a_success_and_scrapers_stay_closed():
    result = run_access(
        plan_for("Sarasota"),
        env=_env(),
        relay_get=_boom,
        stealth_fetch=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no")),
    )
    assert result.attempts[0].reason == "relay_not_configured"
    assert result.ok is False
    assert ManateeCountyScraper.SOURCE_CONTRACT_VALIDATED is False
    assert SarasotaCountyScraper.SOURCE_CONTRACT_VALIDATED is False
    assert ManateeCountyScraper().scrape() == []
    assert SarasotaCountyScraper().scrape() == []


def test_sarasota_listing_person_id_is_not_a_booking_number():
    verdict = assess_listing(LISTING)
    assert verdict.reopen_ok is False
    assert "booking number" in " ".join(verdict.reasons)
    assert "1000000001" not in " ".join(verdict.reasons)


def test_mac_relay_allowlist_rejects_other_counties():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "mac_roster_relay.py"
    spec = importlib.util.spec_from_file_location("mac_roster_relay", path)
    relay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)
    authorized, read_allowlisted = relay.authorized, relay.read_allowlisted

    assert authorized("tok", "tok") is True
    assert authorized("nope", "tok") is False
    assert authorized("", "tok") is False

    def fetch(url):
        assert url == "https://manatee-sheriff.revize.com/bookings"
        return 200, ROSTER

    assert read_allowlisted("Manatee", "FL", fetch) == (200, ROSTER)
    assert read_allowlisted("Hillsborough", "FL", fetch) == (404, "")
    assert read_allowlisted("Lee", "FL", lambda *_a: (_ for _ in ()).throw(AssertionError("fetched"))) == (404, "")
