"""Regression tests — Lee Sheriff origin DNS pin (dead www A-record)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_candidate_origin_ips_prefers_env_and_apex():
    from scrapers import lee_origin as lo

    lo.invalidate_lee_origin_cache()
    with patch.object(lo, "_ENV_ORIGIN_IP", "1.2.3.4"), patch.object(
        lo, "_dns_ips", side_effect=lambda h: {
            "sheriffleefl.org": ["10.0.0.1"],
            "www.sheriffleefl.org": ["10.0.0.2"],
        }.get(h, [])
    ):
        ips = lo.candidate_origin_ips()
    assert ips[0] == "1.2.3.4"
    assert "10.0.0.1" in ips
    assert "10.0.0.2" in ips


def test_discover_pins_first_healthy_ip():
    from scrapers import lee_origin as lo

    lo.invalidate_lee_origin_cache()
    with patch.object(lo, "candidate_origin_ips", return_value=["9.9.9.9", "8.8.8.8"]), patch.object(
        lo, "_probe_ip", side_effect=lambda ip, timeout=10.0: ip == "8.8.8.8"
    ):
        ip = lo.discover_lee_origin_ip(force=True)
    assert ip == "8.8.8.8"
    assert lo.lee_resolve_entries(ip) == ["www.sheriffleefl.org:443:8.8.8.8"]


def test_lee_curl_options_empty_when_no_ip():
    from scrapers import lee_origin as lo

    lo.invalidate_lee_origin_cache()
    with patch.object(lo, "discover_lee_origin_ip", return_value=None):
        assert lo.lee_curl_options() == {}


def test_lee_api_get_builds_absolute_url():
    from scrapers import lee_origin as lo

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    with patch.object(
        lo, "lee_resolve_entries", return_value=["www.sheriffleefl.org:443:1.1.1.1"]
    ), patch("curl_cffi.requests.Session", return_value=mock_session):
        resp = lo.lee_api_get(
            "/public-api/bookings", params={"limit": 1}, max_retries=1
        )

    assert resp is mock_resp
    called_url = mock_session.get.call_args[0][0]
    assert called_url.startswith("https://www.sheriffleefl.org/public-api/bookings")
    assert "limit=1" in called_url
    mock_session.close.assert_called()


def test_live_lee_origin_bookings_smoke():
    """Live smoke: origin pin must reach the public bookings API."""
    from scrapers.lee_origin import discover_lee_origin_ip, lee_api_get

    ip = discover_lee_origin_ip(force=True)
    if not ip:
        pytest.skip("Lee origin unreachable from this network")
    resp = lee_api_get("/public-api/bookings", params={"inCustody": "true", "limit": 1}, max_retries=2)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
