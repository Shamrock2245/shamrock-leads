"""
Unit tests for Traccar device auto-provisioning script and setup router.
"""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.traccar_auto_provision import generate_traccar_config
from dashboard.services.traccar_client import booking_to_unique_id


def test_booking_to_unique_id():
    uid = booking_to_unique_id("LEE-2026-00123")
    assert uid == "shamrock-LEE-2026-00123"


def test_generate_traccar_config():
    config = generate_traccar_config("shamrock-LEE-2026-00123", public_host="leads.shamrockbailbonds.biz")
    assert config["unique_id"] == "shamrock-LEE-2026-00123"
    assert config["server_url"] == "http://leads.shamrockbailbonds.biz:5055"
    assert "org.traccar.client://" in config["deeplink"]
    assert "url=http%3A%2F%2Fleads.shamrockbailbonds.biz%3A5055" in config["deeplink"]
    assert "id=shamrock-LEE-2026-00123" in config["deeplink"]
    assert config["setup_url"].endswith("/traccar/setup/shamrock-LEE-2026-00123")
