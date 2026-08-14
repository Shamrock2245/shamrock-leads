import logging
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from dashboard.main import _unhandled_exception


SENSITIVE_EXCEPTION = (
    "Call 239-555-0199 at 123 Palm Tree Road; "
    "token=super-secret-token mongodb://admin:db-pass@private-db:27017/client_records"
)


def _request(path: str = "/api/private/failure", query: bytes = b"token=query-secret") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("testserver", 443),
            "path": path,
            "raw_path": path.encode(),
            "query_string": query,
            "headers": [],
            "client": ("127.0.0.1", 1234),
        }
    )


def test_unhandled_exception_response_is_generic_json_with_request_id(caplog):
    test_app = FastAPI()
    test_app.add_exception_handler(Exception, _unhandled_exception)

    @test_app.get("/api/private/failure")
    async def raise_private_failure():
        raise RuntimeError(SENSITIVE_EXCEPTION)

    with caplog.at_level(logging.ERROR, logger="dashboard.main"):
        with TestClient(test_app, raise_server_exceptions=False) as client:
            response = client.get("/api/private/failure?token=query-secret")

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/json"
    payload = response.json()
    assert payload == {
        "error": "An unexpected error occurred.",
        "request_id": payload["request_id"],
    }
    uuid.UUID(payload["request_id"])

    response_text = response.text
    for private_value in (
        "239-555-0199",
        "123 Palm Tree Road",
        "super-secret-token",
        "mongodb://",
        "private-db",
        "RuntimeError",
        "query-secret",
    ):
        assert private_value not in response_text

    record = caplog.records[-1]
    assert record.correlation_id == payload["request_id"]
    assert record.route_path == "/api/private/failure"
    assert "/api/private/failure" in record.getMessage()
    assert "query-secret" not in record.getMessage()


@pytest.mark.asyncio
async def test_unhandled_exception_structured_diagnostics_are_redacted(caplog):
    with caplog.at_level(logging.ERROR, logger="dashboard.main"):
        await _unhandled_exception(_request(), ValueError(SENSITIVE_EXCEPTION))

    record = caplog.records[-1]
    diagnostics = f"{record.exception_details}\n{record.exception_traceback}"
    assert "[REDACTED_PHONE]" in diagnostics
    assert "[REDACTED_ADDRESS]" in diagnostics
    assert "[REDACTED_SECRET]" in diagnostics
    assert "[REDACTED_DATABASE_URI]" in diagnostics
    for private_value in (
        "239-555-0199",
        "123 Palm Tree Road",
        "super-secret-token",
        "mongodb://",
        "private-db",
    ):
        assert private_value not in diagnostics
