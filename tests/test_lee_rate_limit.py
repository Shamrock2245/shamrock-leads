"""Unit tests — Lee public-api rate-limit coordination + scrape honesty."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from scrapers import lee_rate_limit as rl


def setup_function(_fn=None):
    # Isolate each test from durable /tmp state left by other runs.
    rl.reset_for_tests(state_path="")


def teardown_function(_fn=None):
    rl.reset_for_tests(state_path="")


def test_record_429_trips_cooldown():
    wait = rl.record_429("Throttled 481697 over (INTERVAL 12 HOUR | 480000)", cooldown_s=60)
    assert wait >= 59
    assert rl.is_cooled_down() is True
    st = rl.cooldown_status()
    assert st["cooled_down"] is True
    assert "481697" in (st["last_429_detail"] or "")


def test_note_response_429():
    resp = MagicMock()
    resp.status_code = 429
    resp.text = "<h1>Too Many Requests (#429)</h1> Throttled 100 over (INTERVAL 12 HOUR | 480000)"
    assert rl.note_response(resp) is True
    assert rl.is_cooled_down() is True


def test_note_response_200_ok():
    rl.clear_cooldown()
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "[]"
    assert rl.note_response(resp) is False
    assert rl.is_cooled_down() is False


def test_clear_cooldown():
    rl.record_429("x", cooldown_s=999)
    assert rl.is_cooled_down()
    rl.clear_cooldown()
    assert not rl.is_cooled_down()


def test_scrape_raises_during_cooldown_instead_of_empty():
    """Cooldown skips must surface as errors, not silent status=empty."""
    from scrapers.counties.lee import LeeCountyScraper

    rl.record_429("unit-test throttle", cooldown_s=120)
    try:
        try:
            LeeCountyScraper().scrape()
            assert False, "expected RuntimeError during cooldown"
        except RuntimeError as exc:
            assert "rate-limit cooldown" in str(exc)
    finally:
        rl.clear_cooldown()


def test_cooldown_persists_across_reset_and_auto_clears(tmp_path: Path):
    """Durable file survives process-local clear+reload; expires self-heal."""
    state = tmp_path / "lee_cooldown.json"
    rl.reset_for_tests(state_path=str(state))
    rl.record_429("persist-me", cooldown_s=120)
    assert state.exists()
    payload = json.loads(state.read_text())
    assert payload["cooled_until"] > time.time()

    # Simulate fresh process: wipe memory, keep file, reload via is_cooled_down.
    rl._cooled_until = 0.0  # noqa: SLF001 — test seam
    rl._loaded_from_disk = False  # noqa: SLF001
    assert rl.is_cooled_down() is True

    # Expire window → auto-clear file (self-heal).
    rl._cooled_until = time.time() - 1  # noqa: SLF001
    payload["cooled_until"] = time.time() - 1
    state.write_text(json.dumps(payload))
    rl._loaded_from_disk = False  # noqa: SLF001
    assert rl.is_cooled_down() is False
    assert not state.exists()


def test_scrape_raises_when_fetch_fails_with_no_ok_pages():
    """Total fetch failure must be status=error, not silent empty."""
    from scrapers.counties.lee import LeeCountyScraper

    scraper = LeeCountyScraper()

    def boom(_start, _end):
        scraper._fetch_ok_pages = 0
        scraper._fetch_errors = ["page1:HTTP_503"]
        scraper._fetch_rate_limited = False
        return []

    with patch.object(scraper, "_fetch_arrests", side_effect=boom):
        try:
            scraper.scrape()
            assert False, "expected RuntimeError on fetch failure"
        except RuntimeError as exc:
            assert "fetch failed" in str(exc)


def test_scrape_true_empty_when_ok_pages_but_no_rows():
    """200 OK with empty roster is a real empty — not an error."""
    from scrapers.counties.lee import LeeCountyScraper

    scraper = LeeCountyScraper()

    def empty_ok(_start, _end):
        scraper._fetch_ok_pages = 2
        scraper._fetch_errors = []
        scraper._fetch_rate_limited = False
        return []

    with patch.object(scraper, "_fetch_arrests", side_effect=empty_ok):
        assert scraper.scrape() == []
