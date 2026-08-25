"""County + state identity — Lee FL is never Lee GA."""
from dashboard.services.place_identity import (
    is_lee_florida,
    mongo_place_clause,
    parse_place,
    places_match,
)
from dashboard.services.packet_builder_service import is_lee_county
from dashboard.services.matching_engine import select_returning_indemnitor_match


def test_parse_place_labeled_and_bare():
    assert parse_place("Lee (FL)") == ("Lee", "FL")
    assert parse_place("Lee County", "Georgia") == ("Lee", "GA")
    assert parse_place("Lee County, SC") == ("Lee", "SC")
    assert parse_place("Lee") == ("Lee", "")


def test_lee_florida_not_other_lees():
    assert is_lee_florida("Lee") is True
    assert is_lee_florida("Lee County") is True
    assert is_lee_florida("Lee (FL)") is True
    assert is_lee_county("Lee", "FL") is True
    assert is_lee_florida("Lee", "GA") is False
    assert is_lee_county("Lee", "GA") is False
    assert is_lee_florida("Lehigh") is False
    assert is_lee_florida("Lee", "SC") is False
    assert is_lee_florida("Lee", "NC") is False


def test_places_match_requires_same_state_when_known():
    assert places_match("Lee", "FL", "Lee County", "") is True
    assert places_match("Lee", "FL", "Lee", "GA") is False
    assert places_match("Lee", "GA", "Lee County", "Georgia") is True
    assert places_match("Collier", "FL", "Lee", "FL") is False


def test_mongo_place_clause_fl_allows_missing_state():
    clause = mongo_place_clause("Lee (FL)")
    blob = str(clause)
    assert "Lee" in blob
    assert "$exists" in blob
    ga = mongo_place_clause("Lee", "GA")
    assert "$exists" not in str(ga)
    assert "GA" in str(ga)


def test_returning_indemnitor_does_not_auto_link_other_state():
    prior = [{"full_name": "DOE, JOHN", "indemnitor_name": "Jane Doe", "county": "Lee", "state": "FL"}]
    live = [{"full_name": "DOE, JOHN", "booking_number": "GA-1", "county": "Lee", "state": "GA"}]
    picked = select_returning_indemnitor_match(
        "Jane Doe", prior, live, preferred_county="Lee", preferred_state="FL"
    )
    assert picked["auto_link"] is False
    assert picked["reason"] == "no_live_booking_for_prior_defendant"

    live_fl = [{"full_name": "DOE, JOHN", "booking_number": "FL-1", "county": "Lee", "state": "FL"}]
    ok = select_returning_indemnitor_match(
        "Jane Doe", prior, live_fl, preferred_county="Lee", preferred_state="FL"
    )
    assert ok["auto_link"] is True
    assert ok["best"]["booking_number"] == "FL-1"
