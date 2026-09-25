"""
Shared exactly-once DocuSeal completion handler
(dashboard/services/docuseal_completion.py), used by BOTH:
  - POST /api/webhooks/docuseal submission.completed (dashboard/routers/webhooks.py)
  - LifecycleAutomations.run_docuseal_poller (dashboard/services/lifecycle_automations.py)

Everything is mocked: in-memory Mongo double, fake DocuSeal/Drive, fake Slack
client, AsyncMock court seed / share invoice / legacy payment link / SSE.
No network, no customer sends. HMAC is exercised for real with a throwaway
test secret.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from collections import Counter, defaultdict
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import dashboard.services.docuseal_completion as dc
import dashboard.services.swipesimple_invoice_service as ss
from dashboard.routers.webhooks import webhooks_bp
from dashboard.services.lifecycle_automations import LifecycleAutomations
from tests._fake_mongo import FakeCollection

TEST_HMAC_SECRET = "docuseal-test-secret-not-real"
BOND_ID = "BC-TEST-0002"
BOOKING = "TEST-BK-0002"
SUBMISSION_ID = 424242
PACKET_ID = "pkt-test-0002"
DEFENDANT = "Quinn Testcase"
SLACK_URL = "https://hooks.example.invalid/services/TEST"
DRIVE_URL = "https://drive.example.invalid/file/d/test-1"


def _packet(**overrides) -> dict:
    pkt = {
        "_id": "oid-test-0002",
        "packet_id": PACKET_ID,
        "bond_case_id": BOND_ID,
        "booking_number": BOOKING,
        "defendant_name": DEFENDANT,
        "surety_id": "palmetto",
        "esign_provider": "docuseal",
        "status": "pending_signature",
        "docuseal_status": "sent",
        "docuseal_submission_id": SUBMISSION_ID,
    }
    pkt.update(overrides)
    return pkt


def _body(event: str = "submission.completed") -> bytes:
    return json.dumps(
        {"event_type": event, "timestamp": "2026-09-25T12:00:00Z",
         "data": {"id": SUBMISSION_ID, "status": "completed"}}
    ).encode("utf-8")


def _sig(body: bytes, secret: str = TEST_HMAC_SECRET) -> str:
    ts = int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"{ts}.{mac}"


class Harness:
    """All side-effect doubles + a shared in-memory DB for one scenario."""

    def __init__(self, packet: Optional[dict] = None, *, drive_ok: bool = True, slow: float = 0.0):
        self.colls: Dict[str, FakeCollection] = defaultdict(FakeCollection)
        self.colls["paperwork_packets"] = FakeCollection([packet or _packet()])
        self.calls: Counter = Counter()
        self.order: List[str] = []
        self.drive_ok = drive_ok
        self.slow = slow
        self.gate: Optional[asyncio.Event] = None  # slow work waits on this if set
        self.slack_posts: List[dict] = []
        h = self

        class FakeDocuSeal:
            is_configured = True

            async def get_submission(self, _sid):
                h.calls["get_submission"] += 1
                return {"status": "completed"}

            async def download_combined_pdf(self, _sid):
                h.order.append("slow_work_start")
                if h.gate is not None:
                    await asyncio.wait_for(h.gate.wait(), timeout=2)
                if h.slow:
                    await asyncio.sleep(h.slow)
                h.calls["download"] += 1
                return b"%PDF-1.4 test"

            def file_signed_pdf_to_drive(self, _pdf, **_kw):
                h.calls["drive_upload"] += 1
                if h.drive_ok:
                    return {"ok": True, "drive_url": DRIVE_URL, "drive_folder_id": "fld-test"}
                return {"ok": False, "error_code": "not_configured", "error": "google_drive_not_configured"}

        class FakeSlackClient:
            def __init__(self, *_a, **_kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_a):
                return False

            async def post(self, url, json=None, **_kw):
                h.calls["slack"] += 1
                h.slack_posts.append({"url": url, "json": json})

        self.FakeDocuSeal = FakeDocuSeal
        self.FakeSlackClient = FakeSlackClient

        async def _share(bond_id, **kw):
            h.calls["share_invoice"] += 1
            await asyncio.sleep(0)
            return {"ok": True, "create": {"idempotent": h.calls["share_invoice"] > 1}, "dispatch": None}

        async def _court(**kw):
            h.calls["court_sync"] += 1
            await asyncio.sleep(0)
            return {"success": True, "reason": "seeded", "gcal": {"status": "created"}}

        async def _pay(**kw):
            h.calls["legacy_payment_link"] += 1
            await asyncio.sleep(0)
            return {"skipped": True, "delivered": False, "reason": "test"}

        async def _publish(*_a, **_kw):
            h.calls["sse_event"] += 1

        self.share = AsyncMock(side_effect=_share)
        self.court = AsyncMock(side_effect=_court)
        self.pay = AsyncMock(side_effect=_pay)
        self.publish = AsyncMock(side_effect=_publish)

    @property
    def packets(self) -> FakeCollection:
        return self.colls["paperwork_packets"]

    def get_col(self, name: str) -> FakeCollection:
        return self.colls[name]

    def packet_doc(self) -> dict:
        return self.packets.docs[0]

    def step(self, name: str) -> dict:
        return ((self.packet_doc().get("docuseal_completion") or {}).get("steps") or {}).get(name) or {}

    def bond_case_updates(self) -> List[tuple]:
        return self.colls["bond_cases"].updates

    @contextmanager
    def patched(self, env: Optional[Dict[str, Optional[str]]] = None):
        env_defaults: Dict[str, Optional[str]] = {
            "DOCUSEAL_WEBHOOK_SECRET": TEST_HMAC_SECRET,
            "SLACK_WEBHOOK_LEADS": SLACK_URL,
            "SLACK_WEBHOOK_URL": None,
            "SWIPESIMPLE_LIVE": None,
            "SWIPESIMPLE_DISPATCH_LIVE": None,
            "DEBUG": None,
            dc.LEGACY_PAYMENT_LINK_ENV: None,
        }
        env_defaults.update(env or {})
        saved = {k: os.environ.get(k) for k in env_defaults}
        for k, v in env_defaults.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            with ExitStack() as st:
                st.enter_context(patch("dashboard.services.docuseal_service.DocuSealService", self.FakeDocuSeal))
                st.enter_context(patch("dashboard.routers.webhooks.get_collection", side_effect=self.get_col))
                st.enter_context(patch("dashboard.routers.events.publish_event", new=self.publish))
                st.enter_context(patch.object(ss, "maybe_issue_share_invoice_for_bond", new=self.share))
                st.enter_context(patch("dashboard.services.bond_court_seed_service.seed_court_calendar_for_bond", new=self.court))
                st.enter_context(patch("dashboard.services.packet_payment_link_service.maybe_send_packet_payment_link", new=self.pay))
                st.enter_context(patch.object(httpx, "AsyncClient", self.FakeSlackClient))
                yield self
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    # ── drivers ────────────────────────────────────────────────────────────
    def app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(webhooks_bp)
        return app

    async def run_poller(self, cfg: Optional[dict] = None) -> dict:
        return await LifecycleAutomations(self.colls).run_docuseal_poller(cfg or {})


async def _asgi_post(app, body: bytes, sig: str, order: List[str], on_response_body=None) -> Dict[str, Any]:
    """Drive the ASGI app directly so we can observe WHEN the response is sent."""
    out: Dict[str, Any] = {"status": None, "body": b""}
    sent_request = False

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)  # pragma: no cover - never needed
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            out["status"] = message["status"]
        elif message["type"] == "http.response.body":
            out["body"] += message.get("body", b"")
            if not message.get("more_body"):
                order.append("response_sent")
                if on_response_body:
                    on_response_body()

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/api/webhooks/docuseal",
        "raw_path": b"/api/webhooks/docuseal", "query_string": b"", "root_path": "",
        "headers": [(b"content-type", b"application/json"), (b"x-docuseal-signature", sig.encode())],
        "client": ("testclient", 50000), "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    out["json"] = json.loads(out["body"] or b"{}")
    return out


EXACTLY_ONCE = ("drive_upload", "share_invoice", "court_sync", "slack", "sse_event")


def _assert_once(h: Harness, legacy: int = 0):
    for k in EXACTLY_ONCE:
        assert h.calls[k] == 1, f"{k} ran {h.calls[k]}x"
    assert h.calls["legacy_payment_link"] == legacy
    assert len([u for u in h.bond_case_updates() if "Packet_Status" in u[1].get("$set", {})]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Claim / concurrency
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("flag,legacy", [(None, 0), ("true", 1)])
def test_concurrent_webhook_redeliveries_single_side_effects(flag, legacy):
    h = Harness(slow=0.02)
    with h.patched(env={dc.LEGACY_PAYMENT_LINK_ENV: flag}):
        async def go():
            app = h.app()
            body = _body()
            return await asyncio.gather(
                _asgi_post(app, body, _sig(body), h.order),
                _asgi_post(app, body, _sig(body), h.order),
            )
        r1, r2 = asyncio.run(go())
    actions = sorted([r1["json"]["action"], r2["json"]["action"]])
    assert r1["status"] == r2["status"] == 200
    assert actions == ["completion_accepted", "completion_duplicate_ignored"]
    assert h.packets.claim_calls == 2
    _assert_once(h, legacy=legacy)
    doc = h.packet_doc()
    assert doc["status"] == "signed" and doc["docuseal_status"] == "completed"
    assert doc["signed_pdf_drive_url"] == DRIVE_URL
    assert doc["docuseal_completion"]["done_at"]
    assert doc["docuseal_completion"]["claim_token"] is None


def test_concurrent_webhook_and_poller_single_side_effects():
    h = Harness(slow=0.02)
    with h.patched(env={dc.LEGACY_PAYMENT_LINK_ENV: "true"}):
        async def go():
            app = h.app()
            body = _body()
            return await asyncio.gather(
                _asgi_post(app, body, _sig(body), h.order),
                h.run_poller(),
            )
        web, poll = asyncio.run(go())
    assert web["status"] == 200
    # exactly one of them won the claim
    won_webhook = web["json"]["action"] == "completion_accepted"
    assert won_webhook != bool(poll["signed"])
    if not won_webhook:
        assert poll["claim_busy"] == 0
    # Flag on + webhook received → legacy link at most once (a poller winner
    # leaves it for the webhook-requested retry: see the lease test below).
    for k in EXACTLY_ONCE:
        assert h.calls[k] == 1, f"{k} ran {h.calls[k]}x"
    assert h.calls["legacy_payment_link"] <= 1


def test_flag_on_poller_then_webhook_fires_legacy_link_once():
    """Flag on ("true" = previous prod behavior): a webhook completion fires the
    legacy link even if the poller finished the packet first — only once."""
    h = Harness()
    with h.patched(env={dc.LEGACY_PAYMENT_LINK_ENV: "true"}):
        res = asyncio.run(h.run_poller())
        assert res["signed"] == 1
        assert h.calls["legacy_payment_link"] == 0          # poller-only: no legacy send
        client = TestClient(h.app())
        body = _body()
        r1 = client.post("/api/webhooks/docuseal", content=body,
                         headers={"Content-Type": "application/json", "X-DocuSeal-Signature": _sig(body)})
        r2 = client.post("/api/webhooks/docuseal", content=body,
                         headers={"Content-Type": "application/json", "X-DocuSeal-Signature": _sig(body)})
    assert r1.json()["action"] == "completion_accepted"
    assert r2.json()["action"] == "completion_duplicate_ignored"
    _assert_once(h, legacy=1)


def test_flag_on_webhook_during_poller_lease_legacy_link_honored_by_next_poll_once():
    """Webhook arrives while the poller holds the lease (claim lost → 200
    duplicate_ignored). The webhook is still recorded, so the next poller run
    sends the legacy link exactly once (flag on)."""
    h = Harness()
    with h.patched(env={dc.LEGACY_PAYMENT_LINK_ENV: "true"}):
        async def go():
            claimed = await dc.claim_completion(h.packets, _packet(), source=dc.SOURCE_POLLER)
            body = _body()
            r = await _asgi_post(h.app(), body, _sig(body), h.order)
            await dc.run_claimed_completion(claimed, get_col=h.get_col, source=dc.SOURCE_POLLER,
                                            submission_id=SUBMISSION_ID)
            first_legacy = h.calls["legacy_payment_link"]
            res = await h.run_poller()
            res2 = await h.run_poller()
            return r, first_legacy, res, res2
        r, first_legacy, res, res2 = asyncio.run(go())
    assert r["json"]["action"] == "completion_duplicate_ignored"
    assert first_legacy == 0
    assert res["completion_retried"] == 1 and res2["scanned"] == 0
    _assert_once(h, legacy=1)
    assert h.pay.await_args.kwargs["source"] == "docuseal_submission_completed"


def test_live_lease_blocks_claim_and_expired_lease_is_reclaimable():
    h = Harness()
    now = datetime.now(timezone.utc)
    with h.patched():
        async def go():
            first = await dc.claim_completion(h.packets, _packet(), source=dc.SOURCE_WEBHOOK, now=now)
            second = await dc.claim_completion(h.packets, _packet(), source=dc.SOURCE_POLLER, now=now)
            later = now + timedelta(seconds=dc.LEASE_SECONDS_DEFAULT + 1)
            third = await dc.claim_completion(h.packets, _packet(), source=dc.SOURCE_POLLER, now=later)
            return first, second, third
        first, second, third = asyncio.run(go())
    assert first is not None
    assert second is None                                   # lease held
    assert third is not None                                # crashed run → lease expired → retry
    assert third["docuseal_completion"]["claim_token"] != first["docuseal_completion"]["claim_token"]
    assert third["docuseal_completion"]["runs"] == 2


def test_crashed_background_run_is_finished_by_poller_after_lease_expiry():
    h = Harness()
    with h.patched():
        async def go():
            claimed = await dc.claim_completion(h.packets, _packet(), source=dc.SOURCE_WEBHOOK)
            assert claimed
            # simulate a crash: nothing ran; age the lease
            h.packet_doc()["docuseal_completion"]["lease_expires_at"] = (
                datetime.now(timezone.utc) - timedelta(seconds=1))
            h.packet_doc()["docuseal_completion"]["webhook_received_at"] = "2026-09-25T12:00:00+00:00"
            return await h.run_poller()
        res = asyncio.run(go())
    assert res["signed"] == 1
    _assert_once(h, legacy=0)   # default flag off → no legacy send even though a webhook arrived


# ─────────────────────────────────────────────────────────────────────────────
# Webhook: 200 before slow work, HMAC unchanged
# ─────────────────────────────────────────────────────────────────────────────

def test_webhook_returns_200_before_slow_work():
    h = Harness()
    with h.patched():
        async def go():
            h.gate = asyncio.Event()
            body = _body()
            # The slow Drive step blocks until the response has been SENT. If the
            # handler did slow work before responding this would time out.
            return await _asgi_post(h.app(), body, _sig(body), h.order, on_response_body=h.gate.set)
        r = asyncio.run(go())
    assert r["status"] == 200 and r["json"]["action"] == "completion_accepted"
    assert h.order.index("response_sent") < h.order.index("slow_work_start")
    _assert_once(h)


def test_background_failure_is_logged_not_raised(caplog):
    caplog.set_level(logging.INFO)
    h = Harness()
    with h.patched(), patch.object(dc._Run, "execute", side_effect=RuntimeError(f"boom {DEFENDANT}")):
        client = TestClient(h.app())
        body = _body()
        r = client.post("/api/webhooks/docuseal", content=body,
                        headers={"Content-Type": "application/json", "X-DocuSeal-Signature": _sig(body)})
    assert r.status_code == 200
    assert "background run failed" in caplog.text and "err_type=RuntimeError" in caplog.text
    assert DEFENDANT not in caplog.text


@pytest.mark.parametrize("sig", ["", "1.deadbeef", f"{int(time.time())}.{'0' * 64}", "sha256=abc"])
def test_hmac_still_fail_closed_no_claim_no_side_effects(sig):
    h = Harness()
    with h.patched():
        client = TestClient(h.app())
        headers = {"Content-Type": "application/json"}
        if sig:
            headers["X-DocuSeal-Signature"] = sig
        r = client.post("/api/webhooks/docuseal", content=_body(), headers=headers)
    assert r.status_code == 401 and r.json() == {"error": "Invalid signature"}
    assert h.packets.claim_calls == 0 and not h.packets.updates
    assert not h.colls["audit_events"].inserts
    assert sum(h.calls.values()) == 0


def test_hmac_fail_closed_when_secret_unset_in_production():
    h = Harness()
    with h.patched(env={"DOCUSEAL_WEBHOOK_SECRET": None, "ENV": "production"}):
        body = _body()
        r = TestClient(h.app()).post(
            "/api/webhooks/docuseal", content=body,
            headers={"Content-Type": "application/json", "X-DocuSeal-Signature": _sig(body)})
    assert r.status_code == 401
    assert h.packets.claim_calls == 0 and sum(h.calls.values()) == 0


def test_form_completed_single_party_does_not_claim():
    h = Harness()
    with h.patched():
        body = _body("form.completed")
        r = TestClient(h.app()).post(
            "/api/webhooks/docuseal", content=body,
            headers={"Content-Type": "application/json", "X-DocuSeal-Signature": _sig(body)})
    assert r.json()["action"] == "party_recorded"
    assert h.packets.claim_calls == 0
    # only the existing per-party SSE event; no completion side effects
    assert h.publish.await_args.args[0] == "docuseal_form_completed"
    assert sum(v for k, v in h.calls.items() if k != "sse_event") == 0


# ─────────────────────────────────────────────────────────────────────────────
# Steps exactly once / retries
# ─────────────────────────────────────────────────────────────────────────────

def test_each_step_exactly_once_across_redelivery_and_poller():
    h = Harness()
    with h.patched():
        client = TestClient(h.app())
        body = _body()
        for _ in range(3):
            client.post("/api/webhooks/docuseal", content=body,
                        headers={"Content-Type": "application/json", "X-DocuSeal-Signature": _sig(body)})
        res1 = asyncio.run(h.run_poller())
        res2 = asyncio.run(h.run_poller())
    _assert_once(h)
    assert res1["scanned"] == 0 and res2["scanned"] == 0
    for step in dc.REQUIRED_STEPS:
        assert h.step(step).get("state") in dc.TERMINAL_STATES, step
    assert h.step(dc.STEP_LEGACY)["state"] == "skipped"   # default OFF


def test_failed_drive_upload_retried_by_poller_other_steps_not_replayed():
    h = Harness(drive_ok=False)
    with h.patched():
        body = _body()
        r = TestClient(h.app()).post(
            "/api/webhooks/docuseal", content=body,
            headers={"Content-Type": "application/json", "X-DocuSeal-Signature": _sig(body)})
        assert r.status_code == 200
        doc = h.packet_doc()
        assert h.step(dc.STEP_DRIVE)["state"] == "failed"
        assert doc["drive_archive_error"]["error_code"] == "not_configured"
        assert doc["status"] == "signed"                     # completion not blocked by Drive
        assert not doc["docuseal_completion"].get("done_at")
        assert h.slack_posts[0]["json"]["text"].endswith("Drive pending")

        h.drive_ok = True
        res = asyncio.run(h.run_poller())
        assert res["completion_retried"] == 1 and res["filed_drive"] == 1
        res2 = asyncio.run(h.run_poller())
        assert res2["scanned"] == 0
    doc = h.packet_doc()
    assert doc["signed_pdf_drive_url"] == DRIVE_URL and doc["drive_archive_error"] is None
    assert h.calls["drive_upload"] == 2                      # 1 failure + 1 retry
    assert h.step(dc.STEP_DRIVE)["state"] == "done"
    assert doc["docuseal_completion"]["done_at"]
    for k in ("share_invoice", "court_sync", "slack", "sse_event"):
        assert h.calls[k] == 1, k
    assert h.calls["legacy_payment_link"] == 0
    # bond_cases got the Drive link backfilled after the retry
    assert any(u[1]["$set"].get("signed_pdf_drive_url") == DRIVE_URL for u in h.bond_case_updates())


def test_drive_skipped_when_link_already_exists():
    h = Harness(_packet(signed_pdf_drive_url=DRIVE_URL))
    with h.patched():
        asyncio.run(dc.handle_docuseal_completion(
            _packet(signed_pdf_drive_url=DRIVE_URL), get_col=h.get_col, source=dc.SOURCE_WEBHOOK,
            submission_id=SUBMISSION_ID))
    assert h.calls["download"] == 0 and h.calls["drive_upload"] == 0
    assert h.step(dc.STEP_DRIVE) == {**h.step(dc.STEP_DRIVE), "state": "skipped", "reason": "drive_link_exists"}


def test_drive_gives_up_after_max_attempts():
    h = Harness(drive_ok=False)
    with h.patched():
        asyncio.run(dc.handle_docuseal_completion(_packet(), get_col=h.get_col, source=dc.SOURCE_POLLER,
                                                  submission_id=SUBMISSION_ID))
        for _ in range(dc.MAX_ATTEMPTS[dc.STEP_DRIVE] + 3):
            asyncio.run(h.run_poller())
    assert h.calls["drive_upload"] == dc.MAX_ATTEMPTS[dc.STEP_DRIVE]
    assert h.step(dc.STEP_DRIVE)["state"] == "gave_up"
    assert h.packet_doc()["drive_archive_error"]["gave_up"] is True
    assert h.packet_doc()["docuseal_completion"]["done_at"]


def test_poller_only_completion_runs_court_bond_cases_slack_once():
    h = Harness()
    with h.patched():
        r1 = asyncio.run(h.run_poller())
        r2 = asyncio.run(h.run_poller())
    assert r1["signed"] == 1 and r1["filed_drive"] == 1
    assert r2["scanned"] == 0
    _assert_once(h, legacy=0)            # default switch: poller path never sends the legacy link
    h.court.assert_awaited_once_with(booking_number=BOOKING, source="docuseal_poller_completed")
    h.share.assert_awaited_once_with(BOND_ID, channel="imessage", dispatch=False,
                                     source="docuseal_poller_completed")
    doc = h.packet_doc()
    assert doc["signnow_status"] == "signed" and doc["status"] == "signed"
    bc = [u for u in h.bond_case_updates() if "Packet_Status" in u[1]["$set"]][0]
    assert bc[0] == {"bond_case_id": BOND_ID} and bc[1]["$set"]["Signature_Status"] == "signed"


def test_share_invoice_fail_closed_error_not_retried_generic_error_retried_capped():
    h = Harness()
    h.share.side_effect = ss.SwipeSimpleInvoiceError("premium_mismatch_vs_bondcase")
    with h.patched():
        asyncio.run(dc.handle_docuseal_completion(_packet(), get_col=h.get_col, source=dc.SOURCE_POLLER,
                                                  submission_id=SUBMISSION_ID))
        asyncio.run(h.run_poller())
    assert h.share.await_count == 1
    assert h.step(dc.STEP_SHARE)["state"] == "done" and h.step(dc.STEP_SHARE)["ok"] is False

    h2 = Harness()
    h2.share.side_effect = ConnectionError("transient")
    with h2.patched():
        asyncio.run(dc.handle_docuseal_completion(_packet(), get_col=h2.get_col, source=dc.SOURCE_POLLER,
                                                  submission_id=SUBMISSION_ID))
        for _ in range(5):
            asyncio.run(h2.run_poller())
    assert h2.share.await_count == dc.MAX_ATTEMPTS[dc.STEP_SHARE]
    assert h2.step(dc.STEP_SHARE)["state"] == "gave_up"
    assert h2.calls["court_sync"] == 1 and h2.calls["slack"] == 1


def test_preexisting_completed_packet_gets_drive_only_backfill():
    """Packet completed by the old code (status=signed, no completion record,
    Drive failed): poller retries Drive only — no retroactive Slack/invoice/court."""
    old = _packet(status="signed", docuseal_status="completed",
                  drive_archive_error={"error_code": "not_configured"})
    h = Harness(old)
    with h.patched():
        res = asyncio.run(h.run_poller())
        res2 = asyncio.run(h.run_poller())
    assert res["filed_drive"] == 1 and res2["scanned"] == 0
    assert h.calls["drive_upload"] == 1
    for k in ("share_invoice", "court_sync", "slack", "sse_event", "legacy_payment_link"):
        assert h.calls[k] == 0, k
    assert h.step(dc.STEP_SLACK)["reason"] == "completed_before_shared_handler"
    assert h.packet_doc()["docuseal_completion"]["preexisting_completion"] is True


def test_stuck_status_refresh_packet_is_picked_up_by_poller():
    """Before the paperwork.py fix, a staff refresh wrote docuseal_status=completed
    on an open packet and the poller skipped it forever."""
    h = Harness(_packet(docuseal_status="completed", status="pending_signature"))
    with h.patched():
        res = asyncio.run(h.run_poller())
    assert res["signed"] == 1
    _assert_once(h, legacy=0)


# ─────────────────────────────────────────────────────────────────────────────
# Legacy payment link switch
# ─────────────────────────────────────────────────────────────────────────────

def test_legacy_switch_default_off_zero_sends_on_completion():
    """BEHAVIOR CHANGE: with the flag unset, neither the webhook nor the poller
    sends the legacy static payment link on DocuSeal completion."""
    assert dc.LEGACY_PAYMENT_LINK_DEFAULT == "off"
    assert dc.LEGACY_PAYMENT_LINK_ENV == "DOCUSEAL_COMPLETION_LEGACY_PAYMENT_LINK"
    h = Harness()
    with h.patched():
        assert os.environ.get(dc.LEGACY_PAYMENT_LINK_ENV) is None
        assert dc._legacy_payment_link_mode() == "off"
        client = TestClient(h.app())
        body = _body()
        for _ in range(2):
            client.post("/api/webhooks/docuseal", content=body,
                        headers={"Content-Type": "application/json", "X-DocuSeal-Signature": _sig(body)})
        asyncio.run(h.run_poller())
    h.pay.assert_not_awaited()
    assert h.step(dc.STEP_LEGACY)["reason"] == "switch_off"
    for k in EXACTLY_ONCE:
        assert h.calls[k] == 1

    hp = Harness()
    with hp.patched():
        asyncio.run(hp.run_poller())
        asyncio.run(hp.run_poller())
    hp.pay.assert_not_awaited()


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "webhook"])
def test_legacy_switch_truthy_enables_webhook_path_once(value):
    h = Harness()
    with h.patched(env={dc.LEGACY_PAYMENT_LINK_ENV: value}):
        client = TestClient(h.app())
        body = _body()
        for _ in range(3):
            client.post("/api/webhooks/docuseal", content=body,
                        headers={"Content-Type": "application/json", "X-DocuSeal-Signature": _sig(body)})
        asyncio.run(h.run_poller())
    h.pay.assert_awaited_once()
    kw = h.pay.await_args.kwargs
    assert kw["packet_id"] == PACKET_ID and kw["booking_number"] == BOOKING
    assert kw["source"] == "docuseal_submission_completed"
    assert kw["packet_doc"]["packet_id"] == PACKET_ID

    hp = Harness()
    with hp.patched(env={dc.LEGACY_PAYMENT_LINK_ENV: value}):
        asyncio.run(hp.run_poller())
    hp.pay.assert_not_awaited()          # "true" = webhook-received completions only


@pytest.mark.parametrize("value", ["off", "OFF", "false", "0"])
def test_legacy_switch_off_prevents_call(value):
    h = Harness()
    with h.patched(env={dc.LEGACY_PAYMENT_LINK_ENV: value}):
        body = _body()
        TestClient(h.app()).post("/api/webhooks/docuseal", content=body,
                                 headers={"Content-Type": "application/json", "X-DocuSeal-Signature": _sig(body)})
        asyncio.run(h.run_poller())
    h.pay.assert_not_awaited()
    assert h.step(dc.STEP_LEGACY) == {**h.step(dc.STEP_LEGACY), "state": "skipped", "reason": "switch_off"}
    for k in EXACTLY_ONCE:
        assert h.calls[k] == 1


def test_legacy_switch_on_via_module_constant_one_line_change():
    h = Harness()
    from dashboard.services import legacy_payment_link_switch as sw

    with h.patched(), patch.object(sw, "LEGACY_PAYMENT_LINK_DEFAULT", "webhook"):
        body = _body()
        TestClient(h.app()).post("/api/webhooks/docuseal", content=body,
                                 headers={"Content-Type": "application/json", "X-DocuSeal-Signature": _sig(body)})
    h.pay.assert_awaited_once()


def test_legacy_switch_all_fires_on_poller_once():
    h = Harness()
    with h.patched(env={dc.LEGACY_PAYMENT_LINK_ENV: "all"}):
        asyncio.run(h.run_poller())
        asyncio.run(h.run_poller())
    h.pay.assert_awaited_once()
    assert h.pay.await_args.kwargs["source"] == "docuseal_poller_completed"


@pytest.mark.parametrize("value", ["sometimes", "maybe", "2", " "])
def test_legacy_switch_unknown_value_fails_closed_off(value):
    with patch.dict(os.environ, {dc.LEGACY_PAYMENT_LINK_ENV: value}):
        assert dc._legacy_payment_link_mode() == "off"


def test_legacy_link_never_retried_after_exception():
    h = Harness()
    h.pay.side_effect = RuntimeError("bluebubbles down")
    with h.patched(env={dc.LEGACY_PAYMENT_LINK_ENV: "true"}):
        client = TestClient(h.app())
        body = _body()
        client.post("/api/webhooks/docuseal", content=body,
                    headers={"Content-Type": "application/json", "X-DocuSeal-Signature": _sig(body)})
        asyncio.run(h.run_poller())
        client.post("/api/webhooks/docuseal", content=body,
                    headers={"Content-Type": "application/json", "X-DocuSeal-Signature": _sig(body)})
    assert h.pay.await_count == 1
    assert h.step(dc.STEP_LEGACY)["state"] == "failed_no_retry"


# ─────────────────────────────────────────────────────────────────────────────
# bond_id resolution (never guess)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"bond_case_id": None}, "missing_bond_id"),
        ({"bond_case_id": ""}, "missing_bond_id"),
        ({"bond_case_id": "BC-1", "bond_id": "BC-9"}, "ambiguous_bond_id"),
        ({"bond_case_id": {"$oid": "x"}}, "ambiguous_bond_id"),
    ],
)
def test_missing_or_ambiguous_bond_id_skips_share_invoice(overrides, reason, caplog):
    caplog.set_level(logging.INFO)
    h = Harness(_packet(**overrides))
    with h.patched():
        asyncio.run(dc.handle_docuseal_completion(_packet(**overrides), get_col=h.get_col,
                                                  source=dc.SOURCE_WEBHOOK, submission_id=SUBMISSION_ID))
        asyncio.run(h.run_poller())
    h.share.assert_not_awaited()
    assert h.step(dc.STEP_SHARE)["state"] == "skipped" and h.step(dc.STEP_SHARE)["reason"] == reason
    assert f"share_invoice stage skipped reason={reason} packet={PACKET_ID}" in caplog.text
    assert h.calls["court_sync"] == 1 and h.calls["slack"] == 1


def test_no_pii_in_handler_logs(caplog):
    caplog.set_level(logging.DEBUG, logger="dashboard.services.docuseal_completion")
    h = Harness(drive_ok=False)
    h.court.side_effect = RuntimeError(f"court fail {DEFENDANT}")
    with h.patched():
        asyncio.run(dc.handle_docuseal_completion(_packet(), get_col=h.get_col, source=dc.SOURCE_WEBHOOK,
                                                  submission_id=SUBMISSION_ID))
    assert DEFENDANT not in caplog.text
    assert "err_type=RuntimeError" in caplog.text


# ─────────────────────────────────────────────────────────────────────────────
# Staff status refresh (paperwork.py) no longer writes docuseal_status=completed
# ─────────────────────────────────────────────────────────────────────────────

class _StatusSvc:
    is_configured = True

    def __init__(self, status):
        self._status = status

    async def get_submission(self, _sid):
        return {"status": self._status, "submitters": [{"role": "indemnitor", "status": self._status}]}

    def normalize_submitter_record(self, s):
        return {"role": s.get("role"), "status": s.get("status")}


@pytest.mark.parametrize("remote,expect_status", [("completed", None), ("pending", "pending")])
def test_status_refresh_does_not_persist_completed(remote, expect_status):
    from dashboard.routers import paperwork as pw

    col = FakeCollection([_packet()])
    with patch.object(pw, "_load_packet", new=AsyncMock(return_value=_packet())), \
         patch.object(pw, "get_collection", return_value=col), \
         patch("dashboard.services.docuseal_service.get_docuseal_service", return_value=_StatusSvc(remote)):
        out = asyncio.run(pw.paperwork_docuseal_status(PACKET_ID))
    assert out["docuseal_status"] == remote                 # UI still sees the live value
    sets = col.updates[-1][1]["$set"]
    assert sets["docuseal_remote_status"] == remote
    if expect_status is None:
        assert "docuseal_status" not in sets
        assert col.docs[0]["docuseal_status"] == "sent"     # poller will still pick it up
    else:
        assert sets["docuseal_status"] == expect_status


# ─────────────────────────────────────────────────────────────────────────────
# Completion-path legacy send: premium_unconfirmed (real service, mocked sends)
# ─────────────────────────────────────────────────────────────────────────────

def test_preexisting_packet_never_gets_legacy_send_even_with_switch_all():
    old = _packet(status="signed", docuseal_status="completed")
    h = Harness(old)
    with h.patched(env={dc.LEGACY_PAYMENT_LINK_ENV: "all"}):
        asyncio.run(h.run_poller())
        asyncio.run(h.run_poller())
    assert h.calls["legacy_payment_link"] == 0
    assert h.step(dc.STEP_LEGACY)["reason"] == "completed_before_shared_handler"


def _real_pay_world(packet: dict):
    """Harness whose legacy step calls the REAL maybe_send_packet_payment_link
    with a BlueBubbles stub (no network)."""
    import dashboard.services.packet_payment_link_service as pls

    h = Harness(packet)
    sent: List[str] = []

    async def fake_bb(phone, msg):
        sent.append(msg)
        return {"status": 200, "message": "sent"}

    @contextmanager
    def patched(env):
        with h.patched(env=env), ExitStack() as st:
            # undo the Harness mock: the legacy step calls the REAL service
            st.enter_context(patch("dashboard.services.packet_payment_link_service.maybe_send_packet_payment_link",
                                   new=_REAL_MAYBE_SEND))
            st.enter_context(patch.object(pls, "get_collection", side_effect=h.get_col))
            st.enter_context(patch.object(pls, "send_message_universal", side_effect=fake_bb))
            gm = st.enter_context(patch("dashboard.services.gmail_reader.GmailReaderService"))
            gm.return_value.is_configured = False
            yield

    return h, sent, patched


import dashboard.services.packet_payment_link_service as _pls_mod  # noqa: E402

_REAL_MAYBE_SEND = _pls_mod.maybe_send_packet_payment_link
_CONFIRMED = {
    "premium_confirmed_amount": 650.0,
    "premium_confirmed_at": "2026-09-24T15:00:00+00:00",
    "premium_confirmed_by": "staff-test",
}


@pytest.mark.parametrize("premium_fields", [
    {},                                                         # nothing
    {"premium_amount": 500.0, "numeric_premium_dollar": "$500.00", "premium": 500.0},  # estimate / prefill
    {**_CONFIRMED, "premium_confirmed_by": None},               # partial marker
])
def test_completion_switch_on_unconfirmed_premium_skips(premium_fields, caplog):
    caplog.set_level(logging.INFO)
    h, sent, patched = _real_pay_world(_packet(indemnitor_phone="2395550142", **premium_fields))
    with patched({dc.LEGACY_PAYMENT_LINK_ENV: "true"}):
        body = _body()
        client = TestClient(h.app())
        for _ in range(2):
            client.post("/api/webhooks/docuseal", content=body,
                        headers={"Content-Type": "application/json", "X-DocuSeal-Signature": _sig(body)})
        asyncio.run(h.run_poller())
    assert sent == []
    st = h.step(dc.STEP_LEGACY)
    assert st["state"] == "skipped" and st["reason"] == "premium_unconfirmed"
    assert "payment_link_send_once" not in h.packet_doc()
    assert "500" not in caplog.text and "2395550142" not in caplog.text


def test_completion_switch_on_confirmed_premium_exactly_one_send():
    h, sent, patched = _real_pay_world(_packet(indemnitor_phone="2395550142", premium_amount=500.0, **_CONFIRMED))
    with patched({dc.LEGACY_PAYMENT_LINK_ENV: "true"}):
        body = _body()
        client = TestClient(h.app())
        for _ in range(3):
            client.post("/api/webhooks/docuseal", content=body,
                        headers={"Content-Type": "application/json", "X-DocuSeal-Signature": _sig(body)})
        asyncio.run(h.run_poller())
        # a later auto path (e.g. finalize) is still blocked by the send-once claim
        later = asyncio.run(_REAL_MAYBE_SEND(packet_id=PACKET_ID, source="packet_finalize"))
    assert len(sent) == 1 and "$650.00" in sent[0] and "$500" not in sent[0]
    assert h.step(dc.STEP_LEGACY)["state"] == "done"
    assert h.packet_doc()["payment_link_send_once"]["state"] == "sent"
    assert later["skipped"] is True and later["reason"] in ("already_sent_once", "recently_sent")


# ─────────────────────────────────────────────────────────────────────────────
# Drive-only backfill (first deploy): idempotent, rate-limited, no replays
# ─────────────────────────────────────────────────────────────────────────────

BACKFILL_DRIVE_URL = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUv/view?usp=drivesdk"
NEVER_REPLAYED = ("share_invoice", "court_sync", "slack", "sse_event", "legacy_payment_link")


def _old(i: int, **kw) -> dict:
    d = _packet(_id=f"oid-old-{i}", packet_id=f"pkt-old-{i}", bond_case_id=f"BC-OLD-{i}",
                booking_number=f"OLD-BK-{i}", status="signed", docuseal_status="completed",
                docuseal_submission_id=900 + i)
    d.update(kw)
    return d


def _backfill_harness(packets: List[dict], **kw) -> Harness:
    h = Harness(packets[0], **kw)
    h.colls["paperwork_packets"] = FakeCollection(packets)
    return h


def _no_replays(h: Harness):
    for k in NEVER_REPLAYED:
        assert h.calls[k] == 0, f"{k} replayed"
    assert not [u for u in h.bond_case_updates() if "Packet_Status" in u[1].get("$set", {})]
    h.pay.assert_not_awaited()


def test_backfill_idempotent_dedups_on_drive_link_and_file_id_and_rerun_uploads_nothing():
    packets = [
        _old(1),                                                     # needs upload
        _old(2, drive_link=BACKFILL_DRIVE_URL),                      # has link
        _old(3, signed_pdf_drive_url=BACKFILL_DRIVE_URL),            # has link
        _old(4, signed_pdf_drive_file_id="1AbCdEfGhIjKlMnOpQrStUv"),  # has file id only
        _old(5, signed_pdf_drive_id="fld-legacy"),                   # legacy id field only
    ]
    h = _backfill_harness(packets)
    h.FakeDocuSeal.file_signed_pdf_to_drive = (
        lambda self, _pdf, **_kw: (h.calls.update(["drive_upload"]) or
                                   {"ok": True, "drive_url": BACKFILL_DRIVE_URL, "drive_folder_id": "fld-x"}))
    with h.patched(env={dc.LEGACY_PAYMENT_LINK_ENV: "all"}), \
         patch("dashboard.services.lifecycle_automations._backfill_pause", new=AsyncMock()):
        r1 = asyncio.run(h.run_poller({"drive_backfill_delay_seconds": 0}))
        r2 = asyncio.run(h.run_poller({"drive_backfill_delay_seconds": 0}))
        r3 = asyncio.run(h.run_poller({"drive_backfill_delay_seconds": 0}))
    assert h.calls["drive_upload"] == 1
    assert r1["drive_backfill"]["uploaded"] == 1
    assert r2["drive_backfill"]["uploaded"] == 0 and r3["drive_backfill"]["uploaded"] == 0
    assert r2["drive_backfill"]["scanned"] == 0
    first = h.packets.docs[0]
    assert first["signed_pdf_drive_url"] == BACKFILL_DRIVE_URL
    assert first["signed_pdf_drive_file_id"] == "1AbCdEfGhIjKlMnOpQrStUv"
    for d in h.packets.docs[1:]:
        assert "docuseal_completion" not in d                        # never even claimed
    _no_replays(h)


def test_backfill_handler_level_dedup_on_file_id():
    """Even if a packet slips past the query, the handler skips on a file id."""
    h = Harness(_old(1, signed_pdf_drive_file_id="1AbCdEfGhIjKlMnOpQrStUv"))
    with h.patched():
        asyncio.run(dc.handle_docuseal_completion(h.packet_doc(), get_col=h.get_col,
                                                  source=dc.SOURCE_POLLER, submission_id=901))
    assert h.calls["download"] == 0 and h.calls["drive_upload"] == 0
    assert h.step(dc.STEP_DRIVE)["reason"] == "drive_file_id_exists"


def test_backfill_rate_limited_bounded_batch_and_delay():
    packets = [_old(i) for i in range(1, 8)]
    h = _backfill_harness(packets)
    sleep = AsyncMock()
    with h.patched(), patch("dashboard.services.lifecycle_automations._backfill_pause", new=sleep):
        r1 = asyncio.run(h.run_poller({"drive_backfill_batch": 3, "drive_backfill_delay_seconds": 1.5}))
        assert h.calls["drive_upload"] == 3
        assert r1["drive_backfill"]["uploaded"] == 3
        assert r1["drive_backfill"]["deferred"] >= 1
        assert sleep.await_count == 2 and all(c.args == (1.5,) for c in sleep.await_args_list)
        r2 = asyncio.run(h.run_poller({"drive_backfill_batch": 3, "drive_backfill_delay_seconds": 1.5}))
        r3 = asyncio.run(h.run_poller({"drive_backfill_batch": 3, "drive_backfill_delay_seconds": 1.5}))
        r4 = asyncio.run(h.run_poller({"drive_backfill_batch": 3, "drive_backfill_delay_seconds": 1.5}))
    assert [r["drive_backfill"]["uploaded"] for r in (r2, r3, r4)] == [3, 1, 0]
    assert h.calls["drive_upload"] == 7                              # each exactly once
    _no_replays(h)


def test_backfill_default_limits_and_clamping():
    assert dc.drive_backfill_limits({}) == (10, 2.0)
    assert dc.drive_backfill_limits(None) == (10, 2.0)
    assert dc.drive_backfill_limits({"drive_backfill_batch": 5000, "drive_backfill_delay_seconds": 999}) == (50, 30.0)
    assert dc.drive_backfill_limits({"drive_backfill_batch": -3, "drive_backfill_delay_seconds": -1}) == (1, 0.0)
    assert dc.drive_backfill_limits({"drive_backfill_batch": "x", "drive_backfill_delay_seconds": "y"}) == (10, 2.0)
    assert dc.drive_backfill_limits({"drive_backfill_delay_seconds": 0}) == (10, 0.0)


def test_backfill_failed_upload_retries_count_against_batch_and_no_replays():
    packets = [_old(i) for i in range(1, 4)]
    h = _backfill_harness(packets, drive_ok=False)
    with h.patched(), patch("dashboard.services.lifecycle_automations._backfill_pause", new=AsyncMock()):
        r1 = asyncio.run(h.run_poller({"drive_backfill_batch": 2}))
        r2 = asyncio.run(h.run_poller({"drive_backfill_batch": 2}))
    assert r1["drive_backfill"]["failed"] == 2 and r1["drive_backfill"]["uploaded"] == 0
    # retries of pre-handler packets are still "backfill": bounded by the batch
    assert r2["drive_backfill"]["scanned"] == 2
    assert h.calls["drive_upload"] == 4
    _no_replays(h)


def test_backfill_kill_switch_uploads_nothing_including_retries():
    packets = [_old(1), _old(2)]
    h = _backfill_harness(packets, drive_ok=False)
    with h.patched(), patch("dashboard.services.lifecycle_automations._backfill_pause", new=AsyncMock()):
        asyncio.run(h.run_poller({}))                 # one failed backfill attempt each
        before = h.calls["drive_upload"]
        h.drive_ok = True
        r = asyncio.run(h.run_poller({"drive_backfill_legacy": False}))
        r_ftd = asyncio.run(h.run_poller({"file_to_drive": False}))
    assert before == 2
    assert h.calls["drive_upload"] == before          # kill switch: no new uploads, no retries
    assert r["drive_backfill"]["enabled"] is False and r["drive_backfill"]["scanned"] == 0
    assert r_ftd["drive_backfill"]["enabled"] is False
    _no_replays(h)


def test_backfill_summary_log_counts_no_pii(caplog):
    caplog.set_level(logging.INFO)
    packets = [_old(1), _old(2, drive_link=BACKFILL_DRIVE_URL)]
    h = _backfill_harness(packets)
    with h.patched(), patch("dashboard.services.lifecycle_automations._backfill_pause", new=AsyncMock()):
        asyncio.run(h.run_poller({}))
    lines = [r.getMessage() for r in caplog.records if "drive_backfill summary" in r.getMessage()]
    assert len(lines) == 1
    line = lines[0]
    for key in ("scanned=1", "uploaded=1", "skipped_existing=0", "failed=0"):
        assert key in line, line
    for needle in (DEFENDANT, "OLD-BK-", BACKFILL_DRIVE_URL, "1AbCdEf"):
        assert needle not in line


def test_backfill_disabled_logs_kill_switch(caplog):
    caplog.set_level(logging.INFO)
    h = _backfill_harness([_old(1)])
    with h.patched():
        asyncio.run(h.run_poller({"drive_backfill_legacy": False}))
    assert "drive_backfill disabled (kill switch)" in caplog.text
    assert h.calls["drive_upload"] == 0


def test_drive_file_id_from_url():
    assert dc.drive_file_id_from_url(BACKFILL_DRIVE_URL) == "1AbCdEfGhIjKlMnOpQrStUv"
    assert dc.drive_file_id_from_url("https://drive.google.com/open?id=1AbCdEfGhIjKlMnOpQrStUv") == "1AbCdEfGhIjKlMnOpQrStUv"
    assert dc.drive_file_id_from_url("") is None and dc.drive_file_id_from_url(None) is None
