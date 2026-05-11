"""
ShamrockLeads — Phase 4: Matching Engine
=========================================
Matches incoming intake records against existing ArrestLead records
in MongoDB using a multi-strategy confidence scoring approach.

Match Strategies (in order of priority):
  1. Exact: County + Booking Number (100% confidence)
  2. Strong: Normalized name + DOB + County (95% confidence)
  3. Fuzzy: Levenshtein name match + DOB (80–90% confidence)
  4. Weak: Name only, no DOB (50–60% confidence)

Auto-links when confidence >= AUTO_LINK_THRESHOLD (default: 80).
Returns candidates for manual review when confidence < threshold.
"""
import logging
import unicodedata
import re
from datetime import datetime, timezone
from typing import Optional

try:
    import jellyfish
    _HAS_JELLYFISH = True
except ImportError:
    _HAS_JELLYFISH = False

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
AUTO_LINK_THRESHOLD = 80   # Auto-link intake → defendant above this score
REVIEW_THRESHOLD = 50      # Surface as candidate above this score

# ── Suffix list (same as defendant_normalizer) ────────────────────────────────
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v", "esq", "phd", "md", "dds"}


def _norm(s: str) -> str:
    """Normalize a name string: strip accents, suffixes, punctuation, lowercase."""
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower().strip()
    s = re.sub(r"[''`]", "", s)
    s = re.sub(r"[-]", " ", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    parts = [p for p in s.split() if p not in _SUFFIXES]
    return " ".join(parts).strip()


def _norm_dob(dob: str) -> str:
    """Normalize DOB to YYYY-MM-DD."""
    if not dob:
        return ""
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(dob.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return dob.strip()


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (0 if ca == cb else 1)))
        prev = curr
    return prev[-1]


def _phonetic_match(a: str, b: str) -> float:
    """Return 0.0–1.0 phonetic similarity using Metaphone + Jaro-Winkler.
    
    Falls back to basic Levenshtein-based similarity if jellyfish is unavailable.
    Compares individual name parts (first, last) for higher accuracy.
    """
    if not _HAS_JELLYFISH:
        return 0.0
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    # Split into parts and compare each
    parts_a = na.split()
    parts_b = nb.split()

    if not parts_a or not parts_b:
        return 0.0

    # Jaro-Winkler on full normalized name
    jw_full = jellyfish.jaro_winkler_similarity(na, nb)

    # Metaphone comparison on individual parts
    metaphone_hits = 0
    total_parts = max(len(parts_a), len(parts_b))
    for pa in parts_a:
        for pb in parts_b:
            if len(pa) < 2 or len(pb) < 2:
                continue
            ma = jellyfish.metaphone(pa)
            mb = jellyfish.metaphone(pb)
            if ma and mb and ma == mb:
                metaphone_hits += 1
                break  # Each part matches at most once

    metaphone_score = metaphone_hits / total_parts if total_parts > 0 else 0.0

    # Blend: 40% Jaro-Winkler + 60% Metaphone match ratio
    return 0.4 * jw_full + 0.6 * metaphone_score


def _name_similarity(a: str, b: str) -> float:
    """Return 0.0–1.0 name similarity score.
    
    Blends Levenshtein edit distance with phonetic matching (jellyfish)
    for robust handling of misspelled and transliterated names.
    """
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    max_len = max(len(na), len(nb))
    dist = _levenshtein(na, nb)
    lev_score = max(0.0, 1.0 - dist / max_len)

    # Blend with phonetic score if available
    phon_score = _phonetic_match(a, b)
    if phon_score > 0:
        # 70% Levenshtein + 30% phonetic for balanced matching
        return 0.7 * lev_score + 0.3 * phon_score

    return lev_score


class MatchResult:
    """Represents a single match candidate."""
    def __init__(
        self,
        arrest_doc: dict,
        confidence: int,
        strategy: str,
        defendant_id: Optional[str] = None,
    ):
        self.arrest_doc = arrest_doc
        self.confidence = confidence
        self.strategy = strategy
        self.defendant_id = defendant_id or arrest_doc.get("defendant_id")
        self.booking_number = arrest_doc.get("booking_number", "")
        self.county = arrest_doc.get("county", "")
        self.full_name = arrest_doc.get("full_name", "")
        self.bond_amount = arrest_doc.get("bond_amount", 0)
        self.charges = arrest_doc.get("charges", "")
        self.booking_date = arrest_doc.get("booking_date", "")

    def to_dict(self) -> dict:
        return {
            "confidence": self.confidence,
            "strategy": self.strategy,
            "defendant_id": self.defendant_id,
            "booking_number": self.booking_number,
            "county": self.county,
            "full_name": self.full_name,
            "bond_amount": self.bond_amount,
            "charges": self.charges,
            "booking_date": str(self.booking_date) if self.booking_date else "",
        }


class MatchingEngine:
    """
    Async matching engine that links intake records to arrest/defendant records.

    Usage:
        engine = MatchingEngine(db)
        result = await engine.match_intake(intake_doc)
    """

    def __init__(self, db):
        self.db = db

    @property
    def arrests(self):
        return self.db["arrests"]

    @property
    def defendants(self):
        return self.db["defendants"]

    @property
    def intake_queue(self):
        return self.db["intake_queue"]

    # ─────────────────────────────────────────────────────────────────────────
    #  Primary entry point
    # ─────────────────────────────────────────────────────────────────────────
    async def match_intake(self, intake_doc: dict) -> dict:
        """
        Attempt to match an intake record to existing arrest/defendant records.

        Returns:
            {
                "matched": bool,
                "auto_linked": bool,
                "confidence": int,
                "strategy": str,
                "best_match": dict | None,
                "candidates": list[dict],
                "intake_id": str,
            }
        """
        intake_id = intake_doc.get("intake_id", "")
        defendant = intake_doc.get("defendant", {})
        county = (
            defendant.get("county")
            or intake_doc.get("defendant_county")
            or ""
        ).strip()
        booking_number = (
            defendant.get("bookingNumber")
            or intake_doc.get("defendant_booking_number")
            or ""
        ).strip()
        def_name = (
            defendant.get("name")
            or f"{defendant.get('firstName', '')} {defendant.get('lastName', '')}".strip()
            or intake_doc.get("defendant_name", "")
        ).strip()
        def_dob = (defendant.get("dob") or "").strip()

        candidates: list[MatchResult] = []

        # ── Strategy 1: Exact booking number + county ──────────────────────
        if county and booking_number:
            doc = await self.arrests.find_one(
                {"county": {"$regex": f"^{re.escape(county)}$", "$options": "i"},
                 "booking_number": booking_number},
                {"_id": 0},
            )
            if doc:
                candidates.append(MatchResult(doc, 100, "exact_booking"))

        # ── Strategy 2: Normalized name + DOB + county ─────────────────────
        if def_name and def_dob and not candidates:
            norm_dob = _norm_dob(def_dob)
            norm_name = _norm(def_name)
            query: dict = {}
            if county:
                query["county"] = {"$regex": f"^{re.escape(county)}$", "$options": "i"}

            cursor = self.arrests.find(query, {"_id": 0}).limit(500)
            async for doc in cursor:
                doc_name = _norm(doc.get("full_name", ""))
                doc_dob = _norm_dob(str(doc.get("dob", "")))
                if doc_dob == norm_dob and doc_name == norm_name:
                    candidates.append(MatchResult(doc, 95, "name_dob_county"))
                    break

        # ── Strategy 3: Fuzzy name + DOB ───────────────────────────────────
        if def_name and def_dob and not candidates:
            norm_dob = _norm_dob(def_dob)
            norm_name = _norm(def_name)
            query = {}
            if county:
                query["county"] = {"$regex": f"^{re.escape(county)}$", "$options": "i"}

            cursor = self.arrests.find(query, {"_id": 0}).limit(1000)
            best_score = 0
            best_doc = None
            async for doc in cursor:
                doc_dob = _norm_dob(str(doc.get("dob", "")))
                if doc_dob != norm_dob:
                    continue
                sim = _name_similarity(def_name, doc.get("full_name", ""))
                score = int(sim * 90)  # Max 90 for fuzzy
                if score > best_score and score >= REVIEW_THRESHOLD:
                    best_score = score
                    best_doc = doc
            if best_doc:
                candidates.append(MatchResult(best_doc, best_score, "fuzzy_name_dob"))

        # ── Strategy 3.5: Phonetic name match + DOB (jellyfish) ──────────
        if def_name and def_dob and not candidates and _HAS_JELLYFISH:
            norm_dob = _norm_dob(def_dob)
            query = {}
            if county:
                query["county"] = {"$regex": f"^{re.escape(county)}$", "$options": "i"}

            cursor = self.arrests.find(query, {"_id": 0}).limit(500)
            best_score = 0
            best_doc = None
            async for doc in cursor:
                doc_dob = _norm_dob(str(doc.get("dob", "")))
                if doc_dob != norm_dob:
                    continue
                phon = _phonetic_match(def_name, doc.get("full_name", ""))
                if phon >= 0.75:  # Strong phonetic match
                    score = int(phon * 85)  # Max 85 for phonetic + DOB
                    if score > best_score and score >= REVIEW_THRESHOLD:
                        best_score = score
                        best_doc = doc
            if best_doc:
                candidates.append(MatchResult(best_doc, best_score, "phonetic_name_dob"))

        # ── Strategy 4: Name only (weak) ───────────────────────────────────
        if def_name and not candidates:
            norm_name = _norm(def_name)
            query = {}
            if county:
                query["county"] = {"$regex": f"^{re.escape(county)}$", "$options": "i"}

            cursor = self.arrests.find(query, {"_id": 0}).limit(500)
            best_score = 0
            best_doc = None
            async for doc in cursor:
                sim = _name_similarity(def_name, doc.get("full_name", ""))
                score = int(sim * 60)  # Max 60 for name-only
                if score > best_score and score >= REVIEW_THRESHOLD:
                    best_score = score
                    best_doc = doc
            if best_doc:
                candidates.append(MatchResult(best_doc, best_score, "name_only"))

        # ── Sort by confidence ─────────────────────────────────────────────
        candidates.sort(key=lambda c: c.confidence, reverse=True)
        best = candidates[0] if candidates else None

        auto_linked = False
        if best and best.confidence >= AUTO_LINK_THRESHOLD:
            auto_linked = await self._link_intake_to_arrest(
                intake_id=intake_id,
                arrest_doc=best.arrest_doc,
                confidence=best.confidence,
                strategy=best.strategy,
            )

        return {
            "matched": bool(best),
            "auto_linked": auto_linked,
            "confidence": best.confidence if best else 0,
            "strategy": best.strategy if best else "none",
            "best_match": best.to_dict() if best else None,
            "candidates": [c.to_dict() for c in candidates[:5]],
            "intake_id": intake_id,
        }

    # ─────────────────────────────────────────────────────────────────────────
    #  Link intake → arrest record
    # ─────────────────────────────────────────────────────────────────────────
    async def _link_intake_to_arrest(
        self,
        intake_id: str,
        arrest_doc: dict,
        confidence: int,
        strategy: str,
    ) -> bool:
        """Stamp the intake record with the matched arrest's booking number."""
        now = datetime.now(timezone.utc)
        booking_number = arrest_doc.get("booking_number", "")
        county = arrest_doc.get("county", "")
        defendant_id = arrest_doc.get("defendant_id", "")

        update = {
            "matched_booking_number": booking_number,
            "matched_county": county,
            "match_confidence": confidence,
            "match_strategy": strategy,
            "match_timestamp": now,
            "status": "matched",
            "updated_at": now,
        }
        if defendant_id:
            update["matched_defendant_id"] = defendant_id

        result = await self.intake_queue.update_one(
            {"intake_id": intake_id},
            {"$set": update},
        )
        return result.modified_count > 0

    # ─────────────────────────────────────────────────────────────────────────
    #  Manual confirm link
    # ─────────────────────────────────────────────────────────────────────────
    async def confirm_match(
        self,
        intake_id: str,
        booking_number: str,
        county: str,
        agent: str = "staff",
    ) -> dict:
        """
        Manually confirm a match between an intake record and an arrest record.
        Used when staff reviews candidates and selects the correct one.
        """
        arrest_doc = await self.arrests.find_one(
            {"booking_number": booking_number,
             "county": {"$regex": f"^{re.escape(county)}$", "$options": "i"}},
            {"_id": 0},
        )
        if not arrest_doc:
            return {"success": False, "error": f"Arrest not found: {county}/{booking_number}"}

        linked = await self._link_intake_to_arrest(
            intake_id=intake_id,
            arrest_doc=arrest_doc,
            confidence=100,
            strategy="manual_confirm",
        )

        # Also stamp confirmed_by
        await self.intake_queue.update_one(
            {"intake_id": intake_id},
            {"$set": {"confirmed_by": agent, "confirmed_at": datetime.now(timezone.utc)}},
        )

        return {
            "success": linked,
            "intake_id": intake_id,
            "booking_number": booking_number,
            "county": county,
            "strategy": "manual_confirm",
            "confidence": 100,
        }

    # ─────────────────────────────────────────────────────────────────────────
    #  Batch match unmatched intakes
    # ─────────────────────────────────────────────────────────────────────────
    async def batch_match(self, limit: int = 100) -> dict:
        """
        Run matching on all pending/unmatched intake records.
        Returns summary statistics.
        """
        cursor = self.intake_queue.find(
            {"status": {"$in": ["pending", "in_progress"]},
             "matched_booking_number": {"$exists": False}},
        ).limit(limit)

        total = 0
        auto_linked = 0
        candidates_found = 0
        no_match = 0

        async for intake in cursor:
            total += 1
            result = await self.match_intake(intake)
            if result["auto_linked"]:
                auto_linked += 1
            elif result["matched"]:
                candidates_found += 1
            else:
                no_match += 1

        return {
            "total_processed": total,
            "auto_linked": auto_linked,
            "candidates_found": candidates_found,
            "no_match": no_match,
        }
