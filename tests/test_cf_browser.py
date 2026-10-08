"""Exit-IP preflight (scrapers/cf_browser.check_exit_ip) and removed stealth launcher."""

import pytest

import scrapers.cf_browser as cfb


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _fake_client(payload, seen=None):
    class _Client:
        def __init__(self, **kw):
            if seen is not None:
                seen.append(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return _Resp(200, payload)

    return _Client


def test_datacenter_exit_is_not_residential(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "Client", _fake_client(
        {"ip": "45.95.160.57", "org": "AS212238 Datacamp Limited", "country": "BS"}))
    info = cfb.check_exit_ip(None, timeout=1, retries=1)
    assert info["ok"] and not info["residential_likely"]


def test_us_isp_exit_is_residential_and_trust_env_is_passed(monkeypatch):
    import httpx

    seen = []
    monkeypatch.setattr(httpx, "Client", _fake_client(
        {"ip": "73.1.2.3", "org": "AS7922 Comcast Cable Communications", "country": "US"}, seen))
    info = cfb.check_exit_ip(None, timeout=1, retries=1, trust_env=False)
    assert info["residential_likely"] and not info["exit_unverified"]
    assert seen and seen[0]["trust_env"] is False and "proxy" not in seen[0]


@pytest.mark.parametrize("name", [
    "launch_cf_browser", "new_stealth_context", "wait_past_cloudflare",
    "_launch_sync_playwright", "require_residential_exit",
])
def test_stealth_launcher_is_removed(name):
    # Last user (Charlotte) moved to stock Playwright, no proxy (2026-10-07).
    assert not hasattr(cfb, name)
