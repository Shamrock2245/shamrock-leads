"""Tests for chat-agent packet staging API (/api/agent/stage-packet & /api/agent/parse-booking-url)."""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.routers.agent_packet import router as agent_packet_router
from tests.bb_fake_mongo import FakeCollection


_PUBLIC_IP = "93.184.216.34"


def _fake_resolve(mapping=None):
    """Resolver stub: hosts in ``mapping`` get those addresses, IP literals and
    numeric forms map through ``ipaddress`` semantics, anything else is public."""
    import ipaddress
    import socket

    mapping = mapping or {}

    def _resolve(host, port):
        if host in mapping:
            return list(mapping[host])
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            pass
        try:
            # inet_aton accepts decimal / hex / short IPv4 like getaddrinfo does
            return [socket.inet_ntoa(socket.inet_aton(host))]
        except OSError:
            return [_PUBLIC_IP]

    return _resolve


import pytest


@pytest.fixture(autouse=True)
def _stub_dns(monkeypatch):
    monkeypatch.setattr("dashboard.services.public_url_guard._resolve", _fake_resolve())


def _make_client():
    app = FastAPI()
    app.include_router(agent_packet_router)
    return TestClient(app)


def test_stage_packet_requires_auth(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    monkeypatch.delenv("LEADS_INTERNAL_TOKEN", raising=False)
    client = _make_client()

    resp = client.post("/api/agent/stage-packet", json={})
    assert resp.status_code == 401

    resp_bad = client.post(
        "/api/agent/stage-packet",
        headers={"X-API-Key": "wrong"},
        json={},
    )
    assert resp_bad.status_code == 401


def test_stage_packet_blocks_ssrf_urls(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()

    for bad_url in (
        "http://127.0.0.1:8000/secret",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost/admin",
        "file:///etc/passwd",
    ):
        resp = client.post(
            "/api/agent/stage-packet",
            headers={"X-API-Key": "secret-gas-key"},
            json={"booking_url": bad_url},
        )
        assert resp.status_code == 400, f"Expected 400 for {bad_url}, got {resp.status_code}"


def test_stage_packet_merges_booking_url_id_ocr_and_chat_input(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    monkeypatch.setenv("SUPER_CRM_URL", "https://leads.shamrockbailbonds.biz")
    client = _make_client()

    cols = {
        "arrests": FakeCollection("arrests"),
        "intake_queue": FakeCollection("intake_queue"),
        "paperwork_packets": FakeCollection("paperwork_packets"),
    }

    fake_booking = {
        "success": True,
        "parse_method": "lee_api",
        "data": {
            "full_name": "JANE MARIE DOE",
            "first_name": "Jane",
            "last_name": "Doe",
            "booking_number": "26-009911",
            "county": "Lee",
            "state": "FL",
            "dob": "1992-04-12",
            "total_bond": 5000.0,
            "charges": [
                {
                    "charge": "GRAND THEFT >$750 <$5,000",
                    "bond_amount": 5000.0,
                    "case_number": "26-CF-001122",
                }
            ],
            "case_number": "26-CF-001122",
        },
    }
    fake_ocr = {
        "success": True,
        "data": {
            "full_name": "JOHN ROBERT SMITH",
            "first_name": "JOHN",
            "last_name": "SMITH",
            "dob": "1978-09-01",
            "dl_number": "S530436783210",
            "dl_state": "FL",
            "address": "1528 BROADWAY",
            "city": "FORT MYERS",
            "state": "FL",
            "zip": "33901",
        },
    }

    fake_img_b64 = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xd9").decode()

    with patch("dashboard.routers.agent_packet.get_collection", side_effect=lambda name: cols[name]), \
         patch("dashboard.routers.agent_packet.ingest_url", AsyncMock(return_value=fake_booking)), \
         patch("dashboard.routers.agent_packet.IDScannerService.scan_id_image", AsyncMock(return_value=fake_ocr)):
        resp = client.post(
            "/api/agent/stage-packet",
            headers={"Authorization": "Bearer secret-gas-key"},
            json={
                "booking_url": "https://www.sheriffleefl.org/booking/?id=26009911",
                "indemnitor": {
                    "phone": "2395550199",
                    "email": "john@example.com",
                    "relationship": "Spouse",
                },
                "id_image_base64": fake_img_b64,
                "surety": "palmetto",
                "notes": "Staged from chat",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["status"] == "staged_ready_for_staff"
    assert data["packet_id"].startswith("PKT-")
    assert data["intake_id"].startswith("INT-AGT-")
    assert data["super_crm_url"] == f"https://leads.shamrockbailbonds.biz/?tab=paperwork&packet_id={data['packet_id']}"

    # Verbatim charge & bond preserved
    assert data["defendant"]["full_name"] == "JANE MARIE DOE"
    assert data["defendant"]["booking_number"] == "26-009911"
    assert data["defendant"]["bond_amount"] == 5000.0
    assert data["defendant"]["charges"][0]["charge"] == "GRAND THEFT >$750 <$5,000"

    # Indemnitor merged from chat + ID OCR
    assert data["indemnitor"]["full_name"] == "JOHN ROBERT SMITH"
    assert data["indemnitor"]["dl_number"] == "S530436783210"
    assert data["indemnitor"]["phone"] == "2395550199"
    assert data["indemnitor"]["email"] == "john@example.com"
    assert data["id_ocr_extracted"] is True

    # Never creates a DocuSeal submission directly
    assert len(cols["paperwork_packets"].docs) == 1
    pkt = cols["paperwork_packets"].docs[0]
    assert pkt["status"] == "staged"
    assert pkt["docuseal_submission_id"] is None
    assert pkt["surety_id"] == "palmetto"
    assert len(cols["intake_queue"].docs) == 1
    # Agent input never creates arrest records (scraper source of truth)
    assert len(cols["arrests"].docs) == 0


def test_sl_hydrate_deep_link_opens_paperwork_packet():
    js = (ROOT / "dashboard" / "sl-hydrate.js").read_text(encoding="utf-8")
    assert "packet_id" in js
    assert "SLPaperwork.startFromBond" in js


def test_stage_packet_blocks_numeric_and_wildcard_dns_ssrf(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    monkeypatch.setattr(
        "dashboard.services.public_url_guard._resolve",
        _fake_resolve({
            "169.254.169.254.nip.io": ["169.254.169.254"],
            "internal.example.com": ["10.0.0.5"],
            "cgnat.example.com": ["100.64.0.1"],
            "mixed.example.com": [_PUBLIC_IP, "127.0.0.1"],
        }),
    )
    client = _make_client()
    ingest = AsyncMock(return_value={"success": False})
    with patch("dashboard.routers.agent_packet.ingest_url", ingest):
        for bad_url in (
            "http://2130706433/",
            "http://0x7f000001/",
            "http://127.1/",
            "http://169.254.169.254.nip.io/latest/meta-data/",
            "http://internal.example.com/admin",
            "http://cgnat.example.com/",
            "http://mixed.example.com/",
            "http://user:pw@example.com/",
        ):
            for path in ("/api/agent/stage-packet", "/api/agent/parse-booking-url"):
                resp = client.post(
                    path,
                    headers={"X-API-Key": "secret-gas-key"},
                    json={"booking_url": bad_url},
                )
                assert resp.status_code == 400, f"{path} {bad_url} -> {resp.status_code}"
    ingest.assert_not_awaited()


class _Recorder:
    """Inner transport stub: records each pinned request, answers from ``routes``."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def factory(self):
        import httpx

        def handler(request):
            self.requests.append(request)
            sni = request.extensions.get("sni_hostname")
            return self.routes[sni](request)

        return httpx.MockTransport(handler)


def _pinned_client(recorder):
    import httpx

    from dashboard.services.public_url_guard import PinnedPublicTransport

    transport = PinnedPublicTransport(verify=False, inner_factory=recorder.factory)
    return httpx.AsyncClient(transport=transport, follow_redirects=True)


def test_pinned_transport_connects_to_checked_ip_with_original_host_and_sni(monkeypatch):
    import asyncio

    import httpx

    monkeypatch.setattr(
        "dashboard.services.public_url_guard._resolve",
        _fake_resolve({"jail.example.com": ["93.184.216.34"]}),
    )
    rec = _Recorder({"jail.example.com": lambda r: httpx.Response(200, text="ok")})

    async def run():
        async with _pinned_client(rec) as client:
            return await client.get("https://jail.example.com:8443/booking/1?id=7")

    resp = asyncio.run(run())
    assert resp.status_code == 200
    assert str(resp.request.url) == "https://jail.example.com:8443/booking/1?id=7"
    (sent,) = rec.requests
    assert sent.url.host == "93.184.216.34"
    assert sent.url.port == 8443
    assert sent.url.raw_path == b"/booking/1?id=7"
    assert sent.headers["Host"] == "jail.example.com:8443"
    assert sent.extensions["sni_hostname"] == "jail.example.com"


def test_dns_rebinding_after_check_never_connects_to_loopback(monkeypatch):
    """Public on the check, 127.0.0.1 on every later lookup: no connection is made."""
    import httpx

    from dashboard.services import url_ingest_service
    from dashboard.services.public_url_guard import PinnedPublicTransport

    lookups = []

    def rebinding_resolve(host, port):
        lookups.append(host)
        return ["93.184.216.34"] if len(lookups) == 1 else ["127.0.0.1"]

    monkeypatch.setattr("dashboard.services.public_url_guard._resolve", rebinding_resolve)
    monkeypatch.setattr("config.source_guard.fail_closed_reason", lambda *a, **k: None)
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    rec = _Recorder({"rebind.example.com": lambda r: httpx.Response(200, text="<title>SECRET</title>")})
    monkeypatch.setattr(
        url_ingest_service,
        "_public_only_transport",
        lambda: PinnedPublicTransport(verify=False, inner_factory=rec.factory),
    )
    client = _make_client()
    cols = {
        "arrests": FakeCollection("arrests"),
        "intake_queue": FakeCollection("intake_queue"),
        "paperwork_packets": FakeCollection("paperwork_packets"),
    }
    with patch("dashboard.routers.agent_packet.get_collection", side_effect=lambda name: cols[name]):
        resp = client.post(
            "/api/agent/parse-booking-url",
            headers={"X-API-Key": "secret-gas-key"},
            json={"booking_url": "https://rebind.example.com/booking/1"},
        )
    assert resp.status_code == 422
    assert "SECRET" not in resp.text
    assert len(lookups) == 2  # router check + transport pin; no third lookup at connect
    assert rec.requests == []


def test_redirect_hops_are_revalidated_and_repinned(monkeypatch):
    import asyncio

    import httpx
    import pytest as _pytest

    from dashboard.services.public_url_guard import UnsafeURLError

    monkeypatch.setattr(
        "dashboard.services.public_url_guard._resolve",
        _fake_resolve({
            "a.example.com": ["93.184.216.34"],
            "b.example.org": ["93.184.216.35"],
            "evil.example.net": ["10.0.0.5"],
        }),
    )
    rec = _Recorder({
        "a.example.com": lambda r: httpx.Response(
            302,
            headers={"Location": "https://b.example.org/next" if r.url.path == "/start" else "http://evil.example.net/x"},
        ),
        "b.example.org": lambda r: httpx.Response(200, text="final"),
        "evil.example.net": lambda r: httpx.Response(200, text="SECRET"),
    })

    async def get(path):
        async with _pinned_client(rec) as client:
            return await client.get(f"https://a.example.com{path}")

    resp = asyncio.run(get("/start"))
    assert resp.text == "final"
    assert [(r.url.host, r.headers["Host"], r.extensions["sni_hostname"]) for r in rec.requests] == [
        ("93.184.216.34", "a.example.com", "a.example.com"),
        ("93.184.216.35", "b.example.org", "b.example.org"),
    ]

    rec.requests.clear()
    with _pytest.raises(UnsafeURLError):
        asyncio.run(get("/to-internal"))
    assert [r.extensions["sni_hostname"] for r in rec.requests] == ["a.example.com"]


def test_pinned_transport_real_httpcore_stack_uses_pinned_ip_and_sni(monkeypatch):
    """Through httpx/httpcore with a fake network backend: TCP goes to the pinned
    IP and TLS server_hostname (SNI + cert hostname check) is the original host."""
    import asyncio
    import ssl

    import httpcore
    import httpx
    from httpcore._backends.mock import AsyncMockBackend, AsyncMockStream

    from dashboard.services.public_url_guard import PinnedPublicTransport

    monkeypatch.setattr(
        "dashboard.services.public_url_guard._resolve",
        _fake_resolve({"jail.example.com": ["93.184.216.34"]}),
    )
    seen = {}

    class Stream(AsyncMockStream):
        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            seen["server_hostname"] = server_hostname
            seen["check_hostname"] = ssl_context.check_hostname
            return self

    class Backend(AsyncMockBackend):
        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            seen["tcp"] = (host, port)
            return Stream(list(self._buffer), http2=self._http2)

    raw = [b"HTTP/1.1 200 OK\r\n", b"Content-Length: 2\r\n", b"\r\n", b"ok"]

    def inner():
        t = httpx.AsyncHTTPTransport(verify=True)
        t._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(), network_backend=Backend(raw)
        )
        return t

    async def run():
        transport = PinnedPublicTransport(verify=True, inner_factory=inner)
        async with httpx.AsyncClient(transport=transport) as client:
            return await client.get("https://jail.example.com/booking/1")

    resp = asyncio.run(run())
    assert resp.text == "ok"
    assert seen["tcp"] == ("93.184.216.34", 443)
    assert seen["server_hostname"] == "jail.example.com"
    assert seen["check_hostname"] is True


def test_ingest_url_default_is_not_pinned(monkeypatch):
    from dashboard.services import url_ingest_service

    calls = []
    monkeypatch.setattr(url_ingest_service, "_public_only_transport", lambda: calls.append(1))
    monkeypatch.setattr("config.source_guard.fail_closed_reason", lambda *a, **k: None)

    import asyncio

    import httpx

    real_client = httpx.AsyncClient
    kwargs_seen = {}

    def client_factory(*args, **kwargs):
        kwargs_seen.update(kwargs)
        kwargs["transport"] = httpx.MockTransport(lambda r: httpx.Response(200, text="<html></html>"))
        return real_client(*args, **kwargs)

    monkeypatch.setattr(url_ingest_service.httpx, "AsyncClient", client_factory)
    asyncio.run(url_ingest_service.ingest_url("https://jail.example.com/booking/1"))
    assert calls == []
    assert kwargs_seen["transport"] is None
    assert kwargs_seen["trust_env"] is True


def test_ingest_url_public_only_refuses_redirect_to_internal_host(monkeypatch):
    import asyncio

    import httpx

    from dashboard.services import url_ingest_service
    from dashboard.services.public_url_guard import PinnedPublicTransport

    monkeypatch.setattr(
        "dashboard.services.public_url_guard._resolve",
        _fake_resolve({"jail.example.com": ["93.184.216.34"]}),
    )
    rec = _Recorder({
        "jail.example.com": lambda r: httpx.Response(302, headers={"Location": "http://127.0.0.1:8000/secret"}),
        "127.0.0.1": lambda r: httpx.Response(200, text="<html><title>SECRET</title>Name: X Y</html>"),
    })
    monkeypatch.setattr(
        url_ingest_service,
        "_public_only_transport",
        lambda: PinnedPublicTransport(verify=False, inner_factory=rec.factory),
    )
    monkeypatch.setattr("config.source_guard.fail_closed_reason", lambda *a, **k: None)

    res = asyncio.run(url_ingest_service.ingest_url("https://jail.example.com/booking/1", public_only=True))
    assert res["success"] is False
    assert "SECRET" not in str(res)
    assert [r.extensions["sni_hostname"] for r in rec.requests] == ["jail.example.com"]


def test_stage_packet_does_not_write_arrests_or_leak_exception_text(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()
    cols = {
        "arrests": FakeCollection("arrests"),
        "intake_queue": FakeCollection("intake_queue"),
        "paperwork_packets": FakeCollection("paperwork_packets"),
    }
    boom = AsyncMock(side_effect=RuntimeError("mongodb://admin:hunter2@10.0.0.5 traceback"))
    with patch("dashboard.routers.agent_packet.get_collection", side_effect=lambda name: cols[name]), \
         patch("dashboard.routers.agent_packet.ingest_url", boom), \
         patch("dashboard.routers.agent_packet.IDScannerService.scan_id_image",
               AsyncMock(side_effect=RuntimeError("ocr internal /srv/secret path"))):
        resp = client.post(
            "/api/agent/stage-packet",
            headers={"X-API-Key": "secret-gas-key"},
            json={
                "booking_url": "https://jail.example.com/booking/1",
                "defendant": {"full_name": "Test Person", "booking_number": "TEST-0001", "county": "Lee"},
                "indemnitor": {"full_name": "Test Cosigner", "phone": "2395550100"},
                "id_image_base64": base64.b64encode(b"\xff\xd8\xff\xd9").decode(),
            },
        )
    assert resp.status_code == 200
    body = resp.text
    assert "hunter2" not in body and "traceback" not in body and "/srv/secret" not in body
    assert cols["arrests"].docs == []
    for doc in cols["intake_queue"].docs + cols["paperwork_packets"].docs:
        assert "Booking_Number" not in doc


def test_stage_packet_rejects_oversized_id_image(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    monkeypatch.setattr("dashboard.routers.agent_packet.MAX_ID_IMAGE_B64_CHARS", 16)
    client = _make_client()
    cols = {
        "arrests": FakeCollection("arrests"),
        "intake_queue": FakeCollection("intake_queue"),
        "paperwork_packets": FakeCollection("paperwork_packets"),
    }
    scan = AsyncMock(return_value={"success": True, "data": {}})
    with patch("dashboard.routers.agent_packet.get_collection", side_effect=lambda name: cols[name]), \
         patch("dashboard.routers.agent_packet.IDScannerService.scan_id_image", scan):
        resp = client.post(
            "/api/agent/stage-packet",
            headers={"X-API-Key": "secret-gas-key"},
            json={
                "defendant": {"full_name": "Test Person", "booking_number": "TEST-0002"},
                "id_image_base64": "A" * 64,
            },
        )
    assert resp.status_code == 200
    scan.assert_not_awaited()
    assert any("too large" in w for w in resp.json()["warnings"])
