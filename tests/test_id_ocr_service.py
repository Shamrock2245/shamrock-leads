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


def test_parse_dl_barcode_donor_and_middle():
    sample = """
    ANSI 636000080002DL
    DAQCA1234567
    DCSGARCIA
    DACMARIA
    DADELENA
    DBB19880312
    DBA20300312
    DAG100 MAIN ST
    DAISAN DIEGO
    DAJCA
    DAK92101
    DBC2
    DAU064 in
    DAYBRO
    DDK1
    """
    res = IDOCRService.parse_dl_text(sample)
    assert res.get("first_name") == "Maria"
    assert res.get("middle_name") == "Elena"
    assert res.get("state") == "CA"
    assert res.get("organ_donor") is True
    assert res.get("sex") == "F"


def test_parse_dl_barcode_apostrophe_and_hyphen_names():
    sample = """
    ANSI 636000080002DL
    DAQD1234567
    DCSO'NEILL
    DACBRENDAN
    DADJOHN
    DBB19851130
    DAG1528 BROADWAY
    DAIFORT MYERS
    DAJFL
    DAK33901
    """
    res = IDOCRService.parse_dl_text(sample)
    assert res.get("last_name") == "O'Neill"
    assert res.get("first_name") == "Brendan"
    assert res.get("full_name") == "Brendan John O'Neill"

    hyphen = """
    ANSI 636000080002DL
    DAQCA999
    DCSST-PIERRE
    DACMARIE
    DAG1 MAIN ST
    DAIMIAMI
    DAJFL
    DAK33101
    """
    h = IDOCRService.parse_dl_text(hyphen)
    assert h.get("last_name") == "St-Pierre"
    assert "St-Pierre" in (h.get("full_name") or "")


def test_normalize_person_name_hard_cases():
    from dashboard.services.id_ocr_service import normalize_person_name

    assert normalize_person_name("O'NEILL") == "O'Neill"
    assert normalize_person_name("O’NEILL") == "O'Neill"
    assert normalize_person_name("d'angelo") == "D'Angelo"
    assert normalize_person_name("SMITH-JONES") == "Smith-Jones"
    assert normalize_person_name("mcdonald") == "McDonald"
    assert normalize_person_name("VAN DYKE") == "Van Dyke"


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
