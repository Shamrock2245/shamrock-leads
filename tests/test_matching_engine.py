"""
Tests for Phase 4: Matching Engine (dashboard/services/matching_engine.py)
Uses module-level helper functions (_norm, _levenshtein, _name_similarity)
since MatchingEngine doesn't expose private _normalize_name / _score_match methods.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

# ── Import targets ────────────────────────────────────────────────────────────
from dashboard.services.matching_engine import (
    MatchingEngine,
    _levenshtein,
    _norm,
    _name_similarity,
)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _make_db(arrests=None, intake=None, defendants=None):
    """Build a mock Motor db with configurable collection responses."""
    db = MagicMock()
    arrests_col = MagicMock()
    intake_col = MagicMock()
    defendants_col = MagicMock()

    arrests_col.find_one = AsyncMock(return_value=arrests)
    intake_col.find_one = AsyncMock(return_value=intake)
    intake_col.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    defendants_col.find_one = AsyncMock(return_value=defendants)

    # Support both db["collection"] and db.collection access
    def _getitem(self_inner, name):
        return {
            "arrests": arrests_col,
            "intake_queue": intake_col,
            "defendants": defendants_col,
        }.get(name, MagicMock())

    db.__getitem__ = _getitem
    return db


# ═══════════════════════════════════════════════════════════════════════════════
#  Unit: Levenshtein distance
# ═══════════════════════════════════════════════════════════════════════════════
class TestLevenshtein:
    def test_identical(self):
        assert _levenshtein("smith", "smith") == 0

    def test_single_substitution(self):
        assert _levenshtein("smith", "smyth") == 1

    def test_empty_strings(self):
        assert _levenshtein("", "") == 0

    def test_one_empty(self):
        assert _levenshtein("abc", "") == 3

    def test_transposition(self):
        # "john" → "jonh" requires at least 1 edit
        assert _levenshtein("john", "jonh") >= 1


# ═══════════════════════════════════════════════════════════════════════════════
#  Unit: Name normalization (_norm module-level function)
# ═══════════════════════════════════════════════════════════════════════════════
class TestNorm:
    def test_strips_suffix_jr(self):
        result = _norm("SMITH JR")
        assert "jr" not in result.lower()

    def test_lowercases(self):
        result = _norm("JOHN SMITH")
        assert result == result.lower()

    def test_empty_string(self):
        assert _norm("") == ""

    def test_strips_apostrophe(self):
        result = _norm("O'BRIEN")
        assert "'" not in result

    def test_strips_sr_suffix(self):
        result = _norm("JONES SR")
        assert "sr" not in result.lower()


# ═══════════════════════════════════════════════════════════════════════════════
#  Unit: Name similarity scoring
# ═══════════════════════════════════════════════════════════════════════════════
class TestNameSimilarity:
    def test_identical_names_score_1(self):
        assert _name_similarity("John Smith", "JOHN SMITH") == 1.0

    def test_different_names_score_below_1(self):
        assert _name_similarity("John Smith", "Jane Jones") < 1.0

    def test_empty_names_score_0(self):
        assert _name_similarity("", "") == 0.0

    def test_similar_names_score_high(self):
        # Typo: Smyth vs Smith — should still score > 0.7
        score = _name_similarity("John Smyth", "John Smith")
        assert score > 0.7

    def test_completely_different_score_low(self):
        score = _name_similarity("Alice Johnson", "Robert Williams")
        assert score < 0.5


# ═══════════════════════════════════════════════════════════════════════════════
#  Integration: match_intake — exact booking number match
#  (async tests require pytest-asyncio; skipped if not installed)
# ═══════════════════════════════════════════════════════════════════════════════
try:
    import pytest_asyncio  # noqa: F401
    HAS_ASYNCIO = True
except ImportError:
    HAS_ASYNCIO = False


@pytest.mark.skipif(not HAS_ASYNCIO, reason="pytest-asyncio not installed")
@pytest.mark.asyncio
class TestMatchIntake:
    async def test_exact_booking_match_auto_links(self):
        arrest_doc = {
            "booking_number": "2024-12345",
            "county": "Lee",
            "full_name": "JOHN SMITH",
            "dob": "1990-01-15",
            "defendant_id": "DEF-ABCDE",
            "lead_score": 80,
        }
        intake_doc = {
            "intake_id": "IN-TEST001",
            "defendant_booking_number": "2024-12345",
            "defendant_county": "Lee",
            "defendant": {"name": "John Smith", "dob": "1990-01-15"},
            "indemnitor": {"firstName": "Jane", "lastName": "Smith"},
        }

        db = _make_db(arrests=arrest_doc, intake=intake_doc)
        engine = MatchingEngine(db)

        result = await engine.match_intake(intake_doc)

        assert result["confidence"] >= 85
        assert result["auto_linked"] is True

    async def test_no_match_returns_no_match(self):
        intake_doc = {
            "intake_id": "IN-TEST002",
            "defendant_booking_number": "9999-99999",
            "defendant_county": "Lee",
            "defendant": {"name": "Unknown Person", "dob": ""},
            "indemnitor": {"firstName": "Test", "lastName": "User"},
        }

        db = _make_db(arrests=None, intake=intake_doc)
        engine = MatchingEngine(db)

        # Mock the cursor to return empty results
        arrests_col = db["arrests"]
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        arrests_col.find = MagicMock(return_value=mock_cursor)

        result = await engine.match_intake(intake_doc)
        assert result["confidence"] == 0 or result.get("strategy") == "no_match"
