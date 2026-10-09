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


# 2026-10-09: the relay egress gate accepts US mobile carriers (Brendan's
# T-Mobile hotspot, AS21928) and still rejects datacenter/hosting, VPN and proxy
# exits. Synthetic documentation-range IPs.
_MOBILE = [
    "AS21928 T-Mobile USA, Inc.",
    "AS6167 Cellco Partnership DBA Verizon Wireless",
    "AS20057 AT&T Mobility LLC",
]
_REJECT = [
    "AS36352 ColoCrossing",            # was never matched (mixed-case marker)
    "AS20473 The Constant Company, LLC (Vultr hosting)",
    "AS13335 Cloudflare, Inc.",        # WARP
    "AS9009 M247 Europe SRL",
    "AS39351 Mullvad VPN AB",
    "AS212238 Datacamp Limited",
    "AS14061 DigitalOcean, LLC",
    "AS16509 Amazon.com, Inc.",
    "AS64500 Example Residential Proxy Network",
]


@pytest.mark.parametrize("org", _MOBILE)
def test_us_mobile_carrier_exit_is_accepted(monkeypatch, org):
    import httpx

    monkeypatch.setattr(httpx, "Client", _fake_client({"ip": "198.51.100.7", "org": org, "country": "US"}))
    assert cfb.check_exit_ip(None, timeout=1, retries=1, trust_env=False)["residential_likely"]


@pytest.mark.parametrize("org", _REJECT)
def test_datacenter_vpn_proxy_exits_are_rejected_even_in_us(monkeypatch, org):
    import httpx

    monkeypatch.setattr(httpx, "Client", _fake_client({"ip": "203.0.113.9", "org": org, "country": "US"}))
    assert not cfb.check_exit_ip(None, timeout=1, retries=1, trust_env=False)["residential_likely"]


def test_foreign_mobile_carrier_is_rejected(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "Client", _fake_client(
        {"ip": "198.51.100.8", "org": "AS3320 Deutsche Telekom AG", "country": "DE"}))
    assert not cfb.check_exit_ip(None, timeout=1, retries=1, trust_env=False)["residential_likely"]


@pytest.mark.parametrize("module", ["pinellas", "manatee", "charlotte"])
def test_relay_gates_pass_tmobile_and_block_vpn(monkeypatch, module):
    import importlib

    from scrapers.scraper_resilience import EgressBlocked

    mod = importlib.import_module(f"scrapers.counties.{module}")
    for var in ("PINELLAS_EGRESS_MODE", "MANATEE_EGRESS_MODE", "CHARLOTTE_EGRESS_MODE"):
        monkeypatch.setenv(var, "direct")
    import httpx

    monkeypatch.setattr(httpx, "Client", _fake_client(
        {"ip": "198.51.100.7", "org": "AS21928 T-Mobile USA, Inc.", "country": "US"}))
    assert mod.resolve_egress() == (None, "direct")
    monkeypatch.setattr(httpx, "Client", _fake_client(
        {"ip": "203.0.113.9", "org": "AS39351 Mullvad VPN AB", "country": "US"}))
    with pytest.raises(EgressBlocked):
        mod.resolve_egress()
