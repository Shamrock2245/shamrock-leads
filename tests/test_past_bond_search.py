"""Unit tests for mapping Mongo past bonds onto defendant lead cards."""
from dashboard.services.past_bond_search import (
    bond_as_lead,
    defendant_name_from_bond,
    merge_leads_with_past_bonds,
    unique_past_count,
    _search_filter,
)


def test_ocr_historical_bond_maps_name_amount_poa():
    lead = bond_as_lead(
        {
            "FirstName": "John",
            "LastName": "Smith",
            "LiabilityAmount": 5000,
            "PremiumAmount": 500,
            "PowerNumber": "OSI-12345",
            "CaseNumber": "25CF100",
            "County": "Lee",
            "Charges": "Battery",
            "BondDate": "2024-03-01",
            "IndemnitorName": "Mary Smith",
        },
        "HistoricalBonds",
    )
    assert lead["full_name"] == "Smith, John"
    assert lead["first_name"] == "John"
    assert lead["last_name"] == "Smith"
    assert lead["bond_amount"] == 5000.0
    assert lead["premium_amount"] == 500.0
    assert lead["poa_number"] == "OSI-12345"
    assert lead["booking_number"] == "OSI-12345"
    assert lead["case_number"] == "25CF100"
    assert lead["county"] == "Lee"
    assert lead["charges"] == "Battery"
    assert lead["indemnitor_name"] == "Mary Smith"
    assert lead["is_past_bond"] is True
    assert lead["lead_status"] == "past_bond"
    assert lead["bond_source"] == "HistoricalBonds"


def test_active_bond_nested_indemnitor_and_dollar_string():
    lead = bond_as_lead(
        {
            "defendant_name": "Jane Doe",
            "booking_number": "2025-001",
            "bond_amount": "$7,500.00",
            "county": "Collier",
            "indemnitor": {"name": "Robert Doe", "phone": "2395550100"},
            "charges": [
                {"description": "DWLSR", "bond_amount": 2500},
                {"charge": "Possession"},
            ],
        },
        "active_bonds",
    )
    assert lead["full_name"] == "Jane Doe"
    assert lead["bond_amount"] == 7500.0
    assert lead["indemnitor_name"] == "Robert Doe"
    assert "DWLSR" in lead["charges"]
    assert "Possession" in lead["charges"]
    assert lead["booking_number"] == "2025-001"


def test_nested_defendant_dict_name():
    name = defendant_name_from_bond({"defendant": {"full_name": "Rivera, Ana"}})
    assert name == "Rivera, Ana"


def test_search_filter_matches_name_poa_and_amount():
    filt = _search_filter("OSI-12345")
    or_fields = {next(iter(clause)) for clause in filt["$or"]}
    assert "defendant_name" in or_fields
    assert "PowerNumber" in or_fields
    assert "poa_number" in or_fields
    assert "indemnitor_name" in or_fields

    amount_filt = _search_filter("$5,000")
    amount_clauses = [c for c in amount_filt["$or"] if "bond_amount" in c or "LiabilityAmount" in c]
    assert any(c.get("bond_amount") == 5000.0 for c in amount_clauses)
    assert any(c.get("LiabilityAmount") == 5000.0 for c in amount_clauses)


def test_search_filter_county_is_anded():
    filt = _search_filter("smith", county="Lee (FL)")
    assert "$and" in filt
    assert filt["$and"][0]["$or"]
    county_or = filt["$and"][1]["$or"]
    assert any("Lee" in str(c) for c in county_or)


def test_merge_does_not_bury_live_jail_card():
    live = [{"full_name": "Jane Doe", "booking_number": "2025-001", "bond_amount": 1000}]
    past = [
        {
            "full_name": "Jane Doe",
            "booking_number": "2025-001",
            "bond_amount": 1000,
            "is_past_bond": True,
            "poa_number": "OSI-1",
        },
        {
            "full_name": "Old Client",
            "booking_number": "POA-99",
            "poa_number": "POA-99",
            "bond_amount": 25000,
            "is_past_bond": True,
        },
    ]
    merged = merge_leads_with_past_bonds(live, past, limit=10)
    names = [row["full_name"] for row in merged]
    assert names[0] == "Old Client"
    assert "Jane Doe" in names
    assert sum(1 for row in merged if row["full_name"] == "Jane Doe") == 1
    jane = next(row for row in merged if row["full_name"] == "Jane Doe")
    assert jane.get("is_past_bond") is not True


def test_unique_past_count_skips_live_collisions():
    live = [{"full_name": "Jane Doe", "booking_number": "2025-001"}]
    past = [
        {"full_name": "Jane Doe", "booking_number": "2025-001", "is_past_bond": True},
        {"full_name": "Old Client", "poa_number": "POA-99", "booking_number": "POA-99"},
    ]
    assert unique_past_count(live, past) == 1


def test_merge_respects_limit():
    live = [{"full_name": f"Live {i}", "booking_number": f"L{i}"} for i in range(5)]
    past = [{"full_name": f"Past {i}", "poa_number": f"P{i}", "booking_number": f"P{i}"} for i in range(5)]
    merged = merge_leads_with_past_bonds(live, past, limit=4)
    assert len(merged) == 4
    assert merged[0]["full_name"].startswith("Past")
