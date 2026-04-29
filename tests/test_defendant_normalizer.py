"""
Tests for the Defendant Normalization Service (Phase 2)
"""
import pytest
from dashboard.services.defendant_normalizer import (
    normalize_name_part,
    normalize_dob,
    normalize_phone,
    make_identity_key,
)

def test_normalize_name_part():
    assert normalize_name_part("John") == "john"
    assert normalize_name_part("JOHN") == "john"
    assert normalize_name_part("  John  ") == "john"
    assert normalize_name_part("John Jr.") == "john"
    assert normalize_name_part("John III") == "john"
    assert normalize_name_part("O'Connor") == "o connor"
    assert normalize_name_part("Smith-Jones") == "smith jones"
    assert normalize_name_part("José") == "jose"

def test_normalize_dob():
    assert normalize_dob("1990-01-01") == "1990-01-01"
    assert normalize_dob("01/01/1990") == "1990-01-01"
    assert normalize_dob("1/1/1990") == "1990-01-01"
    assert normalize_dob("12-31-1990") == "1990-12-31"
    assert normalize_dob("invalid") == "invalid"
    assert normalize_dob("") == ""

def test_normalize_phone():
    assert normalize_phone("239-555-1234") == "+12395551234"
    assert normalize_phone("(239) 555-1234") == "+12395551234"
    assert normalize_phone("12395551234") == "+12395551234"
    assert normalize_phone("+12395551234") == "+12395551234"
    assert normalize_phone("invalid") == ""

def test_make_identity_key():
    assert make_identity_key("Smith", "John", "01/01/1990") == "smith:john:1990-01-01"
    assert make_identity_key("O'Connor", "Mary Jane", "1985-12-31") == "o connor:mary jane:1985-12-31"
    assert make_identity_key("Doe Jr.", "Robert", "5/5/2000") == "doe:robert:2000-05-05"
