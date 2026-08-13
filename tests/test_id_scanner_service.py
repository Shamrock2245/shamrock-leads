"""
Unit tests for ID / Driver License / Passport AI Scanner service and endpoints.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.services.id_scanner_service import IDScannerService
from dashboard.routers.indemnitors import router as indemnitors_bp


@pytest.fixture
def test_app():
    app = FastAPI()
    app.include_router(indemnitors_bp)
    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


def test_id_scanner_raw_text_parsing():
    raw_text = """
    FLORIDA DRIVER LICENSE
    1 DOE
    2 JOHN ROBERT
    4b 06/15/1985
    DL D123-456-78-901-0
    8 1234 MAIN ST
    FORT MYERS FL 33901
    SEX M
    EXP 06/15/2028
    """

    res = IDScannerService.parse_raw_text(raw_text)
    assert res["dl_number"] == "D123456789010"
    assert res["dob"] == "1985-06-15"
    assert res["last_name"] == "DOE"
    assert res["first_name"] == "JOHN"
    assert res["middle_name"] == "ROBERT"
    assert res["sex"] == "M"
    assert res["zip"] == "33901"
    assert res["expiration_date"] == "2028-06-15"
    assert res["address"]
    assert "1234 MAIN ST" in res["address"]
    assert res["city"] == "FORT MYERS"
    assert res["state"] == "FL"


def test_id_scanner_scores_upright_dl_higher_than_noise():
    upright = "FLORIDA DRIVER LICENSE\nDOB 01/02/1990\nEXP 01/02/2028\nSEX M\nD123-456-78-901-0\nFORT MYERS FL 33901"
    garbage = "asdf qwer zxcv 111 222"
    assert IDScannerService._score_id_text(upright) > IDScannerService._score_id_text(garbage)
    assert IDScannerService._extracted_field_count({"first_name": "A", "dl_number": "X", "dob": None}) == 2


def test_id_scanner_prepare_applies_exif_and_downscales():
    from io import BytesIO
    from PIL import Image

    img = Image.new("RGB", (4000, 1200), color=(30, 80, 40))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=90)
    out, note = IDScannerService._prepare_image_for_ocr(buf.getvalue(), filename="wide.jpg")
    assert out
    prepared = Image.open(BytesIO(out))
    assert max(prepared.size) <= 2000
    assert "oriented" in note


def test_id_scanner_normalize_fields():
    raw_parsed = {
        "first_name": "Jane",
        "last_name": "Smith",
        "dob": "1992-04-10",
        "dl_number": "S999-888-77-666-0",
        "dl_state": "fl",
        "address": "456 Palm Ave",
        "city": "Tampa",
        "state": "fl",
        "zip": "33602",
        "sex": "f",
    }
    extracted = IDScannerService._normalize_extracted_fields(raw_parsed)
    assert extracted["first_name"] == "Jane"
    assert extracted["last_name"] == "Smith"
    assert extracted["full_name"] == "Jane Smith"
    assert extracted["dl_number"] == "S999888776660"
    assert extracted["dl_state"] == "FL"
    assert extracted["sex"] == "F"


def test_api_scan_id_ocr_endpoint_multipart(client):
    mock_res = {
        "success": True,
        "engine": "openai_vision",
        "extracted": {
            "first_name": "ALICE",
            "last_name": "JOHNSON",
            "full_name": "ALICE JOHNSON",
            "dob": "1990-01-01",
            "dl_number": "J100200300400",
            "dl_state": "FL",
            "address": "789 OAK ST",
            "city": "NAPLES",
            "state": "FL",
            "zip": "34102",
            "sex": "F",
        },
    }

    with patch("dashboard.services.id_scanner_service.IDScannerService.scan_id_image", return_value=mock_res):
        files = {"file": ("dl.jpg", b"fake_image_bytes", "image/jpeg")}
        res = client.post("/api/id/scan-ocr", files=files)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["extracted"]["full_name"] == "ALICE JOHNSON"
        assert data["extracted"]["dl_number"] == "J100200300400"
