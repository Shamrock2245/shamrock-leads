"""Fail-closed tests for POST /api/webhooks/twilio signature validation."""

from __future__ import annotations

import base64
import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.webhooks import (
    verify_twilio_signature,
    webhooks_bp,
)

PUBLIC_URL = "https://leads.shamrockbailbonds.biz/api/webhooks/twilio"
AUTH_TOKEN = "test_twilio_auth_token_not_real"


def _sign(url: str, params: dict[str, str], token: str = AUTH_TOKEN) -> str:
    suffix = "".join(k + v for k, v in sorted(params.items()))
    digest = hmac.new(token.encode("utf-8"), (url + suffix).encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


@pytest.fixture
def twilio_app():
    app = FastAPI()
    app.include_router(webhooks_bp)
    return app


def test_verify_twilio_signature_helper_accepts_valid():
    params = {"From": "+15551234567", "Body": "hello", "MessageSid": "SMabc"}
    sig = _sign(PUBLIC_URL, params)
    assert verify_twilio_signature(AUTH_TOKEN, sig, [PUBLIC_URL], params) is True
    assert verify_twilio_signature(AUTH_TOKEN, "bad", [PUBLIC_URL], params) is False
    assert verify_twilio_signature(AUTH_TOKEN, "", [PUBLIC_URL], params) is False


def test_twilio_webhook_valid_signature_passes(twilio_app):
    params = {"From": "+15551234567", "Body": "ping", "MessageSid": "SMvalid1"}
    sig = _sign(PUBLIC_URL, params)
    mock_col = MagicMock()
    mock_col.insert_one = AsyncMock(return_value=None)

    with patch.dict(
        "os.environ",
        {
            "ENV": "production",
            "TWILIO_AUTH_TOKEN": AUTH_TOKEN,
            "TWILIO_WEBHOOK_PUBLIC_URL": PUBLIC_URL,
        },
        clear=False,
    ), patch(
        "dashboard.routers.webhooks.get_collection", return_value=mock_col
    ), patch(
        "dashboard.routers.events.publish_event", new_callable=AsyncMock
    ) as mock_pub:
        client = TestClient(twilio_app)
        resp = client.post(
            "/api/webhooks/twilio",
            data=params,
            headers={"X-Twilio-Signature": sig},
        )
        assert resp.status_code == 200
        assert resp.text == "<Response></Response>"
        mock_col.insert_one.assert_awaited_once()
        mock_pub.assert_awaited_once()


def test_twilio_webhook_bad_signature_403_no_side_effects(twilio_app):
    params = {"From": "+15551234567", "Body": "nope", "MessageSid": "SMbad"}
    mock_col = MagicMock()
    mock_col.insert_one = AsyncMock(return_value=None)

    with patch.dict(
        "os.environ",
        {
            "ENV": "production",
            "TWILIO_AUTH_TOKEN": AUTH_TOKEN,
            "TWILIO_WEBHOOK_PUBLIC_URL": PUBLIC_URL,
        },
        clear=False,
    ), patch(
        "dashboard.routers.webhooks.get_collection", return_value=mock_col
    ), patch(
        "dashboard.routers.events.publish_event", new_callable=AsyncMock
    ) as mock_pub:
        client = TestClient(twilio_app)
        resp = client.post(
            "/api/webhooks/twilio",
            data=params,
            headers={"X-Twilio-Signature": "not-a-valid-signature"},
        )
        assert resp.status_code == 403
        assert resp.json().get("error") == "Forbidden"
        mock_col.insert_one.assert_not_called()
        mock_pub.assert_not_called()


def test_twilio_webhook_missing_token_production_503(twilio_app):
    mock_col = MagicMock()
    mock_col.insert_one = AsyncMock(return_value=None)

    with patch.dict(
        "os.environ",
        {"ENV": "production", "TWILIO_AUTH_TOKEN": "", "ENVIRONMENT": ""},
        clear=False,
    ), patch(
        "dashboard.routers.webhooks.get_collection", return_value=mock_col
    ), patch(
        "dashboard.routers.events.publish_event", new_callable=AsyncMock
    ) as mock_pub:
        # Ensure ENVIRONMENT cannot override empty ENV production check
        import os
        os.environ.pop("TWILIO_AUTH_TOKEN", None)
        os.environ["ENV"] = "production"
        os.environ.pop("ENVIRONMENT", None)

        client = TestClient(twilio_app)
        resp = client.post(
            "/api/webhooks/twilio",
            data={"From": "+15551234567", "Body": "x"},
        )
        assert resp.status_code == 503
        assert "not configured" in resp.json().get("error", "").lower()
        mock_col.insert_one.assert_not_called()
        mock_pub.assert_not_called()


def test_twilio_webhook_missing_signature_header_403(twilio_app):
    params = {"From": "+15550001111", "Body": "hi"}
    with patch.dict(
        "os.environ",
        {
            "ENV": "production",
            "TWILIO_AUTH_TOKEN": AUTH_TOKEN,
            "TWILIO_WEBHOOK_PUBLIC_URL": PUBLIC_URL,
        },
        clear=False,
    ), patch(
        "dashboard.routers.webhooks.get_collection", return_value=MagicMock(insert_one=AsyncMock())
    ), patch(
        "dashboard.routers.events.publish_event", new_callable=AsyncMock
    ) as mock_pub:
        client = TestClient(twilio_app)
        resp = client.post("/api/webhooks/twilio", data=params)
        assert resp.status_code == 403
        mock_pub.assert_not_called()
