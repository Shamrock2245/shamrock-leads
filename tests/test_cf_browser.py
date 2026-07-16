"""Tests for Cloudflare browser helpers / residential exit preflight."""

from unittest.mock import patch

import pytest

from scrapers.cf_browser import require_residential_exit, check_exit_ip


def test_require_residential_rejects_datacamp():
    fake = {
        "ok": True,
        "ip": "45.95.160.57",
        "org": "AS212238 Datacamp Limited",
        "country": "BS",
        "city": "Lucaya",
        "residential_likely": False,
        "raw": {},
    }
    with patch("scrapers.cf_browser.check_exit_ip", return_value=fake):
        with pytest.raises(RuntimeError, match="NOT usable residential"):
            require_residential_exit("http://warren:x@hub:8000", label="Charlotte")


def test_require_residential_accepts_us_isp():
    fake = {
        "ok": True,
        "ip": "73.1.2.3",
        "org": "AS7922 Comcast Cable Communications",
        "country": "US",
        "city": "Tampa",
        "residential_likely": True,
        "raw": {},
    }
    with patch("scrapers.cf_browser.check_exit_ip", return_value=fake):
        info = require_residential_exit("http://warren:x@hub:8000", label="Charlotte")
    assert info["ip"] == "73.1.2.3"
