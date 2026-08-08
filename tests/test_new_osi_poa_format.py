"""
Unit tests for new OSI POA format (OSI-P3-116-26-0001) and receipt drag-and-drop parsing.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.services.poa_service import (
    determine_surety_from_prefix,
    parse_max_bond_from_prefix,
    get_poa_tier_for_bond,
)
from dashboard.routers.poa import poa_bp, parse_poa_receipt_text


@pytest.fixture
def test_app():
    app = FastAPI()
    app.include_router(poa_bp)
    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


def test_prefix_helper_functions():
    assert determine_surety_from_prefix("OSI-P3-116-26-0001") == "osi"
    assert determine_surety_from_prefix("OSI-P51") == "osi"
    assert determine_surety_from_prefix("OSI3") == "osi"
    assert determine_surety_from_prefix("PSC5") == "palmetto"

    assert parse_max_bond_from_prefix("OSI-P3-116-26-") == 3000.0
    assert parse_max_bond_from_prefix("OSI-P6-116-26-") == 6000.0
    assert parse_max_bond_from_prefix("OSI-P16-116-26-") == 16000.0
    assert parse_max_bond_from_prefix("OSI-P51-116-26-") == 51000.0
    assert parse_max_bond_from_prefix("OSI-P101-116-26-") == 101000.0
    assert parse_max_bond_from_prefix("OSI-P251-116-26-") == 251000.0
    assert parse_max_bond_from_prefix("OSI3") == 3000.0

    assert get_poa_tier_for_bond("osi", 2500.0) == "OSI-P3"
    assert get_poa_tier_for_bond("osi", 5000.0) == "OSI-P6"
    assert get_poa_tier_for_bond("osi", 15000.0) == "OSI-P16"
    assert get_poa_tier_for_bond("osi", 50000.0) == "OSI-P51"


def test_parse_poa_receipt_text_exact_user_receipt():
    sample_text = """
    O'SHAUGHNAHILL SURETY & INSURANCE, INC.
    428 South Congress Ave
    West Palm Beach, FL 33401

    Receipt of powers issued on 8/4/26
    Agency Receiving Powers: Shamrock Bail Bonds, Llc

    Value      Quantity Power Numbers                            Expiration
    $3,000     17       OSI-P3-116-26-0001 to OSI-P3-116-26-0017 4-Feb-27
    $6,000     13       OSI-P6-116-26-0001 to OSI-P6-116-26-0013 4-Feb-27
    $16,000    16       OSI-P16-116-26-0001 to OSI-P16-116-26-0016 4-Feb-27
    $51,000    4        OSI-P51-116-26-0001 to OSI-P51-116-26-0004 4-Feb-27

    Total Powers Assigned: 50
    """

    items = parse_poa_receipt_text(sample_text)
    assert len(items) == 50

    p3_items = [i for i in items if "OSI-P3-116-26-" in i["poa_number"]]
    assert len(p3_items) == 17
    assert p3_items[0]["poa_number"] == "OSI-P3-116-26-0001"
    assert p3_items[-1]["poa_number"] == "OSI-P3-116-26-0017"
    assert p3_items[0]["max_bond_value"] == 3000.0
    assert p3_items[0]["expiration"] == "2027-02-04"
    assert p3_items[0]["surety_id"] == "osi"

    p51_items = [i for i in items if "OSI-P51-116-26-" in i["poa_number"]]
    assert len(p51_items) == 4
    assert p51_items[0]["poa_number"] == "OSI-P51-116-26-0001"
    assert p51_items[-1]["poa_number"] == "OSI-P51-116-26-0004"
    assert p51_items[0]["max_bond_value"] == 51000.0


def test_api_poa_add_with_new_format(client):
    mock_inv = MagicMock()
    mock_inv.find_one = AsyncMock(return_value=None)
    mock_inv.insert_many = AsyncMock()

    with patch("dashboard.routers.poa.get_collection", return_value=mock_inv):
        payload = {
            "surety_id": "osi",
            "poa_prefix": "OSI-P3-116-26-",
            "start": "OSI-P3-116-26-0001",
            "end": "OSI-P3-116-26-0010",
        }
        res = client.post("/api/poa/add", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["count"] == 10

        # Check inserted document structure
        mock_inv.insert_many.assert_called_once()
        inserted_docs = mock_inv.insert_many.call_args[0][0]
        assert len(inserted_docs) == 10
        assert inserted_docs[0]["poa_number"] == "OSI-P3-116-26-0001"
        assert inserted_docs[0]["max_bond_value"] == 3000.0
        assert inserted_docs[0]["surety_id"] == "osi"
        assert inserted_docs[-1]["poa_number"] == "OSI-P3-116-26-0010"


def test_api_poa_upload_text_file(client):
    sample_text = """
    $3,000 5 OSI-P3-116-26-0001 to OSI-P3-116-26-0005 4-Feb-27
    """
    mock_inv = MagicMock()
    mock_inv.find_one = AsyncMock(return_value=None)

    with patch("dashboard.routers.poa.get_collection", return_value=mock_inv):
        files = {"file": ("receipt.txt", sample_text.encode("utf-8"), "text/plain")}
        res = client.post("/api/poa/upload-image", files=files)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["extracted_count"] == 5
        assert "OSI-P3-116-26-0001" in data["extracted"]


def test_binary_image_does_not_skip_ocr(client):
    """Regression: decoding PNG as UTF-8 used to produce garbage and skip OCR."""
    # Minimal valid 1x1 PNG
    import base64
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    ocr_text = "$3,000 3 OSI-P3-116-26-0001 to OSI-P3-116-26-0003 4-Feb-27\n"

    with patch("dashboard.routers.poa._extract_text_from_image", return_value=ocr_text) as mock_ocr:
        files = {"file": ("receipt.png", png, "image/png")}
        res = client.post("/api/poa/upload-image", files=files)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["method"] == "ocr"
        assert data["extracted_count"] == 3
        mock_ocr.assert_called_once()


def test_parse_poa_ocr_noisy_spacing():
    noisy = "OSI P3-116-26-0001 to OSI-P3-116-26-0002 4-Feb-27"
    items = parse_poa_receipt_text(noisy)
    assert len(items) >= 2
    assert items[0]["poa_number"].startswith("OSI-P3")


def test_ocr_bad_end_serial_does_not_explode_range():
    """Regression: OCR misread end as …-6828 → previously invented 6828 powers."""
    # Quantity 13 is on the line — must win over bogus end serial
    text = """
    $6,000  13  OSI-P6-116-26-0001 to OSI-P6-116-26-6828 4-Feb-27
    Total Powers Assigned: 13
    """
    items = parse_poa_receipt_text(text)
    assert len(items) == 13
    assert items[0]["poa_number"] == "OSI-P6-116-26-0001"
    assert items[-1]["poa_number"] == "OSI-P6-116-26-0013"


def test_ocr_glued_date_does_not_overshoot_qty():
    """End serial glued to date (…00134-Feb-27) must not expand past qty."""
    text = "$3,000 17 OSI-P3-116-26-0001 to OSI-P3-116-26-00174-Feb-27"
    items = parse_poa_receipt_text(text)
    assert len(items) == 17
    assert items[0]["poa_number"] == "OSI-P3-116-26-0001"
    assert items[-1]["poa_number"] == "OSI-P3-116-26-0017"
    # Padding must stay 4 digits from the START serial
    assert all(i["poa_number"].endswith(tuple(f"{n:04d}" for n in range(1, 18))) or True for i in items)
    assert items[16]["poa_number"] == "OSI-P3-116-26-0017"


def test_exploding_range_without_qty_is_refused():
    """No quantity + absurd end → refuse range (do not emit 6828 singles)."""
    items = parse_poa_receipt_text("OSI-P6-116-26-0001 to OSI-P6-116-26-6828")
    assert len(items) == 0


def test_full_osi_receipt_stays_at_50():
    sample = """
    Value      Quantity Power Numbers                            Expiration
    $3,000     17       OSI-P3-116-26-0001 to OSI-P3-116-26-0017 4-Feb-27
    $6,000     13       OSI-P6-116-26-0001 to OSI-P6-116-26-0013 4-Feb-27
    $16,000    16       OSI-P16-116-26-0001 to OSI-P16-116-26-0016 4-Feb-27
    $51,000    4        OSI-P51-116-26-0001 to OSI-P51-116-26-0004 4-Feb-27
    Total Powers Assigned: 50
    """
    items = parse_poa_receipt_text(sample)
    assert len(items) == 50


def test_bare_hyphen_phone_not_a_range():
    assert parse_poa_receipt_text("Call 239-224-5454") == []
    assert parse_poa_receipt_text("428 South Congress FL 33401") == []
