"""Unit tests — FL511 ALPR pipeline helpers (no live streams / models required)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services.alpr_engine import normalize_plate, probe_alpr_deps, ALPREngine, PlateDetection
from services.alpr_cameras import load_camera_registry, enabled_cameras, _normalize_cam
from services.alpr_matcher import _normalize_plate as mnorm


def test_normalize_plate():
    assert normalize_plate("ab-123 c") == "AB123C"
    assert normalize_plate("") == ""
    assert mnorm("99xyz") == "99XYZ"


def test_camera_registry_loads():
    cams = load_camera_registry()
    assert isinstance(cams, list)
    assert len(cams) >= 1
    # Without URLs, enabled_cameras may be empty — still valid
    en = enabled_cameras(cams)
    assert isinstance(en, list)


def test_normalize_cam_disables_empty_url():
    c = _normalize_cam({"id": "x", "name": "X", "stream_url": "", "enabled": True})
    assert c["enabled"] is False


def test_probe_alpr_deps_structure():
    p = probe_alpr_deps()
    assert "opencv" in p
    assert "fast_alpr" in p
    assert "engine_ready" in p


def test_engine_parse_fast_alpr_result_object():
    """fast-alpr returns ALPRResult with nested ocr.text + list confidence."""
    eng = ALPREngine(min_confidence=0.5)
    eng._alpr = object()
    eng._load_error = None

    class _BBox:
        x1, y1, x2, y2 = 1, 2, 40, 20

    class _Det:
        bounding_box = _BBox()

    class _Ocr:
        text = "ABC123"
        confidence = [0.9, 0.95, 0.8]
        region = "FL"

    class _Result:
        detection = _Det()
        ocr = _Ocr()

    eng._predict = lambda img: [_Result()]  # type: ignore
    dets = eng.detect_bgr(object())
    assert len(dets) == 1
    assert dets[0].plate_text == "ABC123"
    assert dets[0].confidence > 0.8
    assert dets[0].state == "FL"


def test_engine_parse_dict_results():
    eng = ALPREngine(min_confidence=0.5)
    eng._alpr = object()  # pretend loaded
    eng._load_error = None

    def _fake_predict(img):
        return [
            {"plate": "ABC123", "confidence": 0.91, "bbox": [1, 2, 3, 4]},
            {"plate": "XX", "confidence": 0.2},  # below threshold / short
        ]

    eng._predict = _fake_predict  # type: ignore
    dets = eng.detect_bgr(object())
    assert len(dets) == 1
    assert dets[0].plate_text == "ABC123"
    assert dets[0].confidence == 0.91


def test_matcher_skips_unknown_plate_without_store_all():
    from services import alpr_matcher as am

    db = MagicMock()
    # watchlist empty
    db.__getitem__ = MagicMock(
        side_effect=lambda name: MagicMock(
            find=MagicMock(return_value=[]),
            create_index=MagicMock(),
            insert_one=MagicMock(),
        )
    )
    # Simpler: patch loaders
    with patch.object(am, "load_watchlist_plates", return_value={}), patch.object(
        am, "load_active_bond_plates", return_value={}
    ), patch.object(am, "ensure_indexes"), patch.dict(
        "os.environ", {"ALPR_STORE_ALL_HITS": "false"}, clear=False
    ):
        matcher = am.ALPRMatcher(db=MagicMock())
        matcher._watch = {}
        hit = matcher.match(
            "ZZZ999",
            confidence=0.9,
            state="FL",
            camera={"id": "c1", "name": "Cam"},
        )
        assert hit is None


def test_matcher_hit_on_watchlist():
    from services import alpr_matcher as am

    fake_hits = MagicMock()
    fake_hits.insert_one.return_value = MagicMock(inserted_id="oid1")

    class _DB(dict):
        def __getitem__(self, k):
            if k == "lpr_hits":
                return fake_hits
            return MagicMock()

    with patch.object(am, "ensure_indexes"), patch.object(
        am, "load_watchlist_plates", return_value={}
    ), patch.object(am, "load_active_bond_plates", return_value={}), patch.object(
        am, "send_alpr_slack_alert", return_value=True
    ):
        matcher = am.ALPRMatcher(db=_DB())
        matcher._watch = {
            "ABC123": {
                "defendant_id": "def1",
                "defendant_name": "Test Def",
                "case_number": "26CF1",
            }
        }
        hit = matcher.match(
            "ABC-123",
            confidence=0.88,
            state="FL",
            camera={"id": "cam1", "name": "I-75", "lat": 26.6, "lon": -81.8},
        )
        assert hit is not None
        assert hit["matched"] is True
        assert hit["plate_text"] == "ABC123"
        assert hit["matched_defendant_id"] == "def1"
        fake_hits.insert_one.assert_called_once()


def test_alpr_router_importable():
    from dashboard.routers import alpr as alpr_router

    assert alpr_router.router.prefix == "/api/alpr"
    paths = {getattr(r, "path", None) for r in alpr_router.router.routes}
    assert any(p and "status" in p for p in paths)
    assert any(p and "hits" in p for p in paths)
    assert any(p and "watchlist" in p for p in paths)
    assert any(p and "scan-image" in p for p in paths)
    assert any(p and "snapshot" in p for p in paths)


def test_fl511_jpeg_url_guards_hosts():
    from dashboard.routers.alpr import _fl511_jpeg_url, _registry_stream_list

    assert _fl511_jpeg_url({"id": "fl511_44", "stream_url": "https://fl511.com/map/Cctv/44"}).endswith("/44")
    assert _fl511_jpeg_url({"id": "fl511_44", "stream_url": "/map/Cctv/44"}).startswith("https://fl511.com")
    # Untrusted host falls back to FL511 map endpoint
    url = _fl511_jpeg_url({"id": "fl511_99", "stream_url": "https://evil.example/x.jpg"})
    assert "fl511.com" in url and "99" in url

    merged = _registry_stream_list(
        [{"id": "fl511_1", "name": "Cam", "stream_url": "https://fl511.com/map/Cctv/1", "enabled": True, "stream_type": "jpeg"}],
        {"streams": [{"id": "fl511_1", "connected": True, "frames_ok": 3, "frames_fail": 0}]},
    )
    assert len(merged) == 1
    assert merged[0]["connected"] is True
    assert merged[0]["stream_url"].startswith("https://fl511.com")
