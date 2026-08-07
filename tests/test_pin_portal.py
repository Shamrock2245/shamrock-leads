"""
Tests for Mobile PIN Portal Router (paperwork.shamrockbailbonds.biz)
"""
import pytest
from fastapi.testclient import TestClient
from dashboard.main import app
from dashboard.routers.pin_portal import pin_portal_router

# Ensure router is registered for test runner
app.include_router(pin_portal_router)
client = TestClient(app)


def test_portal_ui_route():
    response = client.get("/api/portal/portal-ui", follow_redirects=True)
    assert response.status_code == 200
    assert "Shamrock Bail Bonds" in response.text
    assert "Mobile E-Sign Portal" in response.text


def test_verify_admin_pin_bypass():
    payload = {"phone": "2395550199", "pin": "224545"}
    response = client.post("/api/portal/verify-pin", json=payload, follow_redirects=True)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["verified"] is True
    assert "PORTAL-ADMIN" in data["session_token"]
