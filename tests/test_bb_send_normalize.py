"""Regression: BlueBubbles send_message_universal result shape must be consistent."""
from dashboard.services.bb_client import normalize_bb_send_result, bb_send_accepted


def test_queued_shape_accepted():
    """Live path returned status/channel=queued without sent/queued booleans — was 503."""
    r = {
        "success": True,
        "status": "queued",
        "channel": "queued",
        "queued_id": "x",
        "error": None,
    }
    n = normalize_bb_send_result(r)
    assert n["success"] is True
    assert n["queued"] is True
    assert n["sent"] is False
    assert bb_send_accepted(n) is True


def test_imessage_direct_send():
    r = {"success": True, "channel": "imessage", "data": {}}
    n = normalize_bb_send_result(r)
    assert n["sent"] is True
    assert n["queued"] is False
    assert bb_send_accepted(n) is True


def test_explicit_sent():
    r = {"success": True, "sent": True, "channel": "imessage"}
    n = normalize_bb_send_result(r)
    assert n["sent"] is True
    assert bb_send_accepted(n) is True


def test_failed():
    r = {"success": False, "error": "boom"}
    n = normalize_bb_send_result(r)
    assert n["success"] is False
    assert bb_send_accepted(n) is False


def test_empty():
    assert bb_send_accepted(None) is False
    assert bb_send_accepted({}) is False
