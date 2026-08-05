"""Unit tests — Lee public-api rate-limit coordination."""
from __future__ import annotations

from unittest.mock import MagicMock

from scrapers import lee_rate_limit as rl


def setup_function(_fn=None):
    rl.clear_cooldown()


def teardown_function(_fn=None):
    rl.clear_cooldown()


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
