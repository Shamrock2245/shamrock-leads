"""
Tests for production auth allowlist hardening, webhook PIN gating, and today_new calculation.
Tracks fix for Issue #19 / Grok audit review.
"""
import os
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from dashboard.main import app
from dashboard.auth.pin_middleware import OPEN_PATHS, is_machine_auth_valid
from writers.mongo_writer import MongoWriter
from core.models import ArrestRecord
from datetime import datetime, timezone

client = TestClient(app)


def test_open_paths_exact_whitelist():
    """Verify OPEN_PATHS only exposes public/safe routes."""
    expected = frozenset({
        "/done",
        "/paperwork",
        "/login",
        "/health",
        "/health/live",
        "/manifest.json",
        "/favicon.ico",
        "/favicon.png",
        "/apple-touch-icon.png",
        "/shamrock-logo.png",
    })
    assert OPEN_PATHS == expected
    assert "/api/stats" not in OPEN_PATHS
    assert "/api/imessage/status" not in OPEN_PATHS
    assert "/docs" not in OPEN_PATHS
    assert "/redoc" not in OPEN_PATHS
    assert "/openapi.json" not in OPEN_PATHS


def test_unauthenticated_api_routes_return_401():
    """Verify sensitive endpoints return 401 when called without auth."""
    # Stats
    resp = client.get("/api/stats")
    assert resp.status_code == 401
    assert resp.json() == {"error": "Authentication required"}

    # iMessage status
    resp = client.get("/api/imessage/status")
    assert resp.status_code == 401

    # OpenAPI schema
    resp = client.get("/openapi.json")
    assert resp.status_code == 401

    # Automation status
    resp = client.get("/api/automation/status")
    assert resp.status_code == 401

    # Webhook status endpoint
    resp = client.get("/api/webhooks/bluebubbles/status")
    assert resp.status_code == 401


def test_docs_redirect_to_login():
    """Verify /docs redirects to /login for unauthenticated users."""
    resp = client.get("/docs", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


def test_health_routes_remain_open():
    """Liveness probes must stay open for load balancers."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json().get("status") in ("healthy", "ok")


def test_machine_auth_allows_sweeps_and_api():
    """Machine keys (GAS_API_KEY / LEADS_INTERNAL_TOKEN) bypass PIN middleware."""
    with patch.dict(os.environ, {"GAS_API_KEY": "test-gas-secret-key-12345"}):
        mock_col = MagicMock()
        mock_col.count_documents = AsyncMock(return_value=10)
        mock_col.distinct = AsyncMock(return_value=["Lee"])
        mock_col.aggregate = MagicMock()

        async def mock_gen():
            yield {"_id": None, "avg_bond": 5000, "max_bond": 10000, "total_bond": 50000}

        mock_col.aggregate.return_value = mock_gen()

        with patch("dashboard.routers.stats.get_collection", return_value=mock_col):
            resp = client.get("/api/stats", headers={"X-API-Key": "test-gas-secret-key-12345"})
            assert resp.status_code == 200
            assert resp.json()["total_arrests"] == 10


def test_today_new_counts_first_seen_and_booking_dates():
    """Verify today_new uses first_seen_at or booking/arrest date rather than bulk created_at."""
    mock_col = MagicMock()
    mock_col.count_documents = AsyncMock(return_value=949)
    mock_col.distinct = AsyncMock(return_value=["Lee"])
    mock_col.aggregate = MagicMock()

    async def mock_gen():
        yield {"_id": None, "avg_bond": 5000, "max_bond": 10000, "total_bond": 50000}

    mock_col.aggregate.return_value = mock_gen()

    with patch("dashboard.routers.stats.get_collection", return_value=mock_col), \
         patch.dict(os.environ, {"GAS_API_KEY": "test-gas-key"}):
        resp = client.get("/api/stats", headers={"X-API-Key": "test-gas-key"})
        assert resp.status_code == 200
        assert resp.json()["today_new"] == 949

        # Verify query structure passed to count_documents
        args = mock_col.count_documents.call_args_list
        today_query = args[1][0][0]
        assert "$or" in today_query
        clauses = today_query["$or"]
        keys = [list(c.keys())[0] for c in clauses]
        assert "first_seen_at" in keys
        assert "booking_date" in keys
        assert "arrest_date" in keys


def test_mongo_writer_strictly_sets_created_at_on_insert():
    """Ensure mongo_writer sets created_at and first_seen_at in $setOnInsert, never in $set."""
    writer = MongoWriter.__new__(MongoWriter)
    writer.arrests = MagicMock()
    writer.scraper_status = MagicMock()
    writer.leads = MagicMock()

    record = ArrestRecord(
        Booking_Number="TEST-12345",
        County="Lee",
        Full_Name="JONES, DAVE",
        Bond_Amount="5000",
    )
    # Inject dirty fields to verify they are stripped from $set
    record.extra_data = {"created_at": "DIRTY", "first_seen_at": "DIRTY"}

    captured_ops = []

    def mock_bulk_write(ops, ordered=False):
        captured_ops.extend(ops)
        mock_result = MagicMock()
        mock_result.bulk_api_result = {"upserted": []}
        mock_result.upserted_count = 1
        mock_result.modified_count = 0
        return mock_result

    writer.arrests.bulk_write = mock_bulk_write

    stats = writer.write_records([record], "Lee")
    assert len(captured_ops) == 1
    op = captured_ops[0]

    # Inspect the UpdateOne operation
    set_doc = op._doc["$set"]
    set_on_insert = op._doc["$setOnInsert"]

    assert "created_at" not in set_doc
    assert "first_seen_at" not in set_doc
    assert "created_at" in set_on_insert
    assert "first_seen_at" in set_on_insert


def test_traccar_device_status_requires_pin_or_token():
    """GET /api/traccar/device-status/{id} must be gated and return 401 without valid auth or token."""
    from dashboard.routers.traccar_setup_api import make_device_status_token
    
    # 1. No token, no session -> 401
    resp = client.get("/api/traccar/device-status/12345")
    assert resp.status_code == 401
    assert resp.json().get("error") == "Authentication required"

    # 2. Invalid token -> 401
    resp_invalid = client.get("/api/traccar/device-status/12345?token=bogus_token")
    assert resp_invalid.status_code == 401

    # 3. Valid HMAC token for that device_id -> 200 (device status accessible to setup page)
    valid_token = make_device_status_token("12345")
    with patch("dashboard.routers.traccar_setup_api.get_collection") as mock_get_col:
        mock_col = MagicMock()
        mock_col.find_one = AsyncMock(return_value={"unique_id": "12345", "last_seen": "2026-09-10T12:00:00Z"})
        mock_get_col.return_value = mock_col
        resp_valid = client.get(f"/api/traccar/device-status/12345?token={valid_token}")
        assert resp_valid.status_code == 200
        assert resp_valid.json().get("connected") is True


def test_traccar_webhook_allowed_without_pin():
    """POST /api/traccar/webhook is PIN-exempt; authenticity is the webhook secret."""
    with patch.dict(os.environ, {"TRACCAR_WEBHOOK_SECRET": "traccar-secret", "ENV": "test"}):
        resp = client.post(
            "/api/traccar/webhook",
            headers={"X-Traccar-Webhook-Secret": "traccar-secret"},
            json={"event": {"type": "deviceOnline", "deviceId": 1}},
        )
    assert resp.status_code == 200
    assert resp.json().get("ok") is True
    assert resp.json().get("type") == "event_only"


def test_traccar_webhook_rejects_missing_or_wrong_secret():
    with patch.dict(os.environ, {"TRACCAR_WEBHOOK_SECRET": "traccar-secret", "ENV": "test"}):
        missing = client.post(
            "/api/traccar/webhook",
            json={"event": {"type": "deviceOnline", "deviceId": 1}},
        )
        wrong = client.post(
            "/api/traccar/webhook",
            headers={"X-Traccar-Webhook-Secret": "nope"},
            json={"event": {"type": "deviceOnline", "deviceId": 1}},
        )
    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_traccar_webhook_fail_closed_when_secret_unset_in_production():
    with patch.dict(os.environ, {
        "TRACCAR_WEBHOOK_SECRET": "",
        "ENV": "production",
        "ENVIRONMENT": "production",
        "REQUIRE_TRACCAR_WEBHOOK_SECRET": "",
    }, clear=False):
        resp = client.post(
            "/api/traccar/webhook",
            json={"event": {"type": "deviceOnline", "deviceId": 1}},
        )
    assert resp.status_code == 503
    assert "secret" in resp.json().get("error", "").lower()


def test_health_endpoint_drops_total_arrests():
    """GET /health must verify connectivity but not publish total_arrests publicly."""
    with patch("dashboard.main.get_collection") as mock_get_col:
        mock_col = MagicMock()
        mock_db = MagicMock()
        mock_db.command = AsyncMock(return_value={"ok": 1})
        mock_col.database = mock_db
        mock_get_col.return_value = mock_col

        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"
        assert "total_arrests" not in data
        assert data.get("database") == "connected"


def test_payment_webhook_rejects_empty_and_invalid_payload():
    """POST /api/webhooks/payment must fail closed on empty/invalid payload."""
    # Empty body -> 400
    resp = client.post("/api/webhooks/payment", json={})
    assert resp.status_code == 400
    assert "payload" in resp.json().get("error", "").lower()

    # Dummy body missing event_type / transaction_id -> 400
    resp = client.post("/api/webhooks/payment", json={"hello": "world"})
    assert resp.status_code == 400
    assert "Missing required fields" in resp.json().get("error", "")


def test_payment_webhook_validates_signature_when_configured():
    """POST /api/webhooks/payment must enforce signature when secret is set."""
    with patch.dict(os.environ, {"SWIPESIMPLE_WEBHOOK_SECRET": "supersecretkey"}):
        # Missing signature
        resp = client.post(
            "/api/webhooks/payment",
            json={"event_type": "payment.completed", "transaction_id": "tx_123"},
        )
        assert resp.status_code == 401
        assert "signature" in resp.json().get("error", "").lower()


def test_wix_intake_webhook_rejects_empty_payload():
    """POST /api/webhooks/wix-intake with valid key but empty body must return 400."""
    with patch.dict(os.environ, {"WIX_WEBHOOK_SECRET": "wix-sec-123"}):
        resp = client.post(
            "/api/webhooks/wix-intake",
            headers={"X-Wix-Webhook-Secret": "wix-sec-123"},
            json={},
        )
        assert resp.status_code == 400
        assert "empty" in resp.json().get("error", "").lower()

