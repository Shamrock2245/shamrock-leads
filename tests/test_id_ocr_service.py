"""
Tests for ID OCR Service (Driver's License & Passport Parsing)
"""
from dashboard.services.id_ocr_service import IDOCRService


def test_parse_dl_barcode():
    sample_aamva = """
    ANSI 636000080002DL00390237DLDAQD123456789012
    DCSDOE
    DACJOHN
    DBB19900515
    DAG1528 BROADWAY
    DAIFORT MYERS
    DAJFL
    DAK33901
    """
    res = IDOCRService.parse_dl_text(sample_aamva)
    assert res.get("first_name") == "John"
    assert res.get("last_name") == "Doe"
    assert res.get("full_name") == "John Doe"
    assert res.get("dl_number") == "D123456789012"
    assert res.get("dob") == "05/15/1990"
    assert res.get("address") == "1528 Broadway"
    assert res.get("city") == "Fort Myers"
    assert res.get("state") == "FL"
    assert res.get("zip") == "33901"


def test_extract_indemnitor_data():
    front_text = "FLORIDA DRIVER LICENSE DOB: 05/15/1990 FORT MYERS FL 33901"
    back_text = "ANSI 636000080002DL00390237DLDAQD123456789012 DCSDOE DACJOHN DBB19900515 DAG1528 BROADWAY DAIFORT MYERS DAJFL DAK33901"

    ind = IDOCRService.extract_indemnitor_data(front_text, back_text)
    assert ind["indemnitor_name"] == "John Doe"
    assert ind["indemnitor_dl"] == "D123456789012"
    assert ind["indemnitor_address"] == "1528 Broadway"
    assert ind["indemnitor_city"] == "Fort Myers"
    assert ind["indemnitor_state"] == "FL"
    assert ind["indemnitor_zip"] == "33901"
    assert ind["ocr_confidence"] == 0.95
