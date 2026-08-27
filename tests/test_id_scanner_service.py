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
    assert res["last_name"] == "Doe"
    assert res["first_name"] == "John"
    assert res["middle_name"] == "Robert"
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


def test_id_scanner_normalize_preserves_apostrophe_last_names():
    extracted = IDScannerService._normalize_extracted_fields({
        "first_name": "BRENDAN",
        "middle_name": "JOHN",
        "last_name": "O’NEILL",
        "full_name": "BRENDAN JOHN O’NEILL",
    })
    assert extracted["last_name"] == "O'Neill"
    assert extracted["full_name"] == "Brendan John O'Neill"


def test_id_scanner_front_line_o_neill():
    raw = "FLORIDA DRIVER LICENSE\n1 O'NEILL\n2 BRENDAN JOHN\n8 1528 BROADWAY\nFORT MYERS FL 33901"
    res = IDScannerService.parse_raw_text(raw)
    assert res["last_name"] == "O'Neill"
    assert res["first_name"] == "Brendan"
    assert "O'Neill" in (res["full_name"] or "")


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
    assert extracted["organ_donor"] is None


def test_id_scanner_does_not_default_state_to_fl():
    extracted = IDScannerService._normalize_extracted_fields({"first_name": "Ana", "last_name": "Cruz"})
    assert extracted["dl_state"] is None
    assert extracted["state"] is None


def test_id_scanner_foreign_passport_mrz():
    raw = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\nL898902C36UTO7408122F1204159ZE184226B<<<<<10"
    res = IDScannerService.parse_raw_text(raw)
    assert res["id_type"] == "passport"
    assert res["issuing_country"] == "UTO"
    assert res["last_name"] == "Eriksson"
    assert res["first_name"] == "Anna"


def test_id_scanner_organ_donor_and_physicals():
    raw = "CALIFORNIA DRIVER LICENSE\nDONOR\nHGT 5-06 EYES BRO HAIR BLK\nCLASS C\nDOB 03/04/1991"
    res = IDScannerService.parse_raw_text(raw)
    assert res["organ_donor"] is True
    assert res["eye_color"] == "BRO"
    assert res["hair_color"] == "BLK"


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
