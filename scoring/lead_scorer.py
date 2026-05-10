"""
Lead Scoring Module — ShamrockLeads

Implements lead qualification scoring for arrest records.
Ported from swfl-arrest-scrapers LeadScorer with imports adapted
for the shamrock-leads project structure.

Scoring Rules:
- Bond amount: 500-50K (+30), 50K-100K (+20), >100K (+10), <500 (-10), 0 (-50)
- Bond type: CASH/SURETY (+25), NO BOND/HOLD (-50), ROR (-30)
- Status: IN CUSTODY (+20), RELEASED (-30)
- Data completeness: All required fields (+15), Missing data (-10)
- Disqualifying charges: capital/murder/federal (-100)
- NLP charge severity: felony_1 (+15), felony_2 (+10), felony_3 (+5)
- FTA risk indicators: high (+15), medium (+8)
- Prior arrest history: 5+ priors (+10), 3+ priors (+5)

Lead Status Mapping:
- score < 0: Disqualified
- score >= 70: Hot
- score >= 40: Warm
- otherwise: Cold
"""

import re
from typing import Tuple, List
from core.models import ArrestRecord


class LeadScorer:
    """
    Lead scoring engine for arrest records.

    Calculates a qualification score and status based on multiple factors
    including bond amount, bond type, custody status, data completeness,
    charge severity, FTA risk, and prior history.
    """

    # Scoring thresholds
    BOND_TIER_1_MIN = 500
    BOND_TIER_1_MAX = 50000
    BOND_TIER_2_MAX = 100000

    # Score thresholds for status
    HOT_THRESHOLD = 70
    WARM_THRESHOLD = 40

    # Disqualifying charge keywords
    DISQUALIFYING_CHARGES = ['capital', 'murder', 'federal']

    def __init__(self):
        """Initialize the lead scorer."""
        self.debug_mode = False
        self.score_breakdown = []

    def score_arrest(self, record: ArrestRecord) -> Tuple[int, str]:
        """
        Calculate the lead score and status for an arrest record.

        Args:
            record: ArrestRecord instance to score

        Returns:
            Tuple of (score: int, status: str)
        """
        self.score_breakdown = []
        total_score = 0

        # 1. Bond amount scoring
        bond_score, bond_reason = self._score_bond_amount(record.Bond_Amount)
        total_score += bond_score
        if bond_reason:
            self.score_breakdown.append(bond_reason)

        # 2. Bond type scoring
        bond_type_score, bond_type_reason = self._score_bond_type(record.Bond_Type)
        total_score += bond_type_score
        if bond_type_reason:
            self.score_breakdown.append(bond_type_reason)

        # 3. Status scoring
        status_score, status_reason = self._score_status(record.Status)
        total_score += status_score
        if status_reason:
            self.score_breakdown.append(status_reason)

        # 4. Data completeness scoring
        completeness_score, completeness_reason = self._score_data_completeness(record)
        total_score += completeness_score
        if completeness_reason:
            self.score_breakdown.append(completeness_reason)

        # 5. Disqualifying charges check
        disqual_score, disqual_reason = self._check_disqualifying_charges(record.Charges)
        total_score += disqual_score
        if disqual_reason:
            self.score_breakdown.append(disqual_reason)

        # 6. NLP-enhanced charge severity scoring
        nlp_score, nlp_reason = self._score_charge_severity(record.Charges)
        total_score += nlp_score
        if nlp_reason:
            self.score_breakdown.append(nlp_reason)

        # 7. FTA risk indicator scoring
        fta_score, fta_reason = self._score_fta_risk(record.Charges)
        total_score += fta_score
        if fta_reason:
            self.score_breakdown.append(fta_reason)

        # 8. Determine lead status based on final score
        lead_status = self._determine_lead_status(total_score)

        return total_score, lead_status

    def _score_bond_amount(self, bond_amount: str) -> Tuple[int, str]:
        """Score based on bond amount."""
        bond_value = self._parse_bond_amount(bond_amount)

        if bond_value is None:
            return 0, ""

        if bond_value == 0:
            return -50, "Bond amount: $0 (-50)"
        elif bond_value < self.BOND_TIER_1_MIN:
            return -10, f"Bond amount: ${bond_value:,.0f} < $500 (-10)"
        elif bond_value <= self.BOND_TIER_1_MAX:
            return 30, f"Bond amount: ${bond_value:,.0f} in $500-$50K range (+30)"
        elif bond_value <= self.BOND_TIER_2_MAX:
            return 20, f"Bond amount: ${bond_value:,.0f} in $50K-$100K range (+20)"
        else:
            return 10, f"Bond amount: ${bond_value:,.0f} > $100K (+10)"

    def _score_bond_type(self, bond_type: str) -> Tuple[int, str]:
        """Score based on bond type."""
        if not bond_type:
            return 0, ""

        bond_type_upper = bond_type.upper()

        if 'NO BOND' in bond_type_upper or 'HOLD' in bond_type_upper:
            return -50, f"Bond type: {bond_type} (NO BOND/HOLD) (-50)"

        if 'ROR' in bond_type_upper or 'R.O.R' in bond_type_upper:
            return -30, f"Bond type: {bond_type} (ROR) (-30)"

        if 'CASH' in bond_type_upper or 'SURETY' in bond_type_upper:
            return 25, f"Bond type: {bond_type} (CASH/SURETY) (+25)"

        return 0, ""

    def _score_status(self, status: str) -> Tuple[int, str]:
        """Score based on custody status."""
        if not status:
            return 0, ""

        status_upper = status.upper()

        if 'IN CUSTODY' in status_upper or 'INCUSTODY' in status_upper:
            return 20, f"Status: {status} (IN CUSTODY) (+20)"

        if 'RELEASED' in status_upper:
            return -30, f"Status: {status} (RELEASED) (-30)"

        return 0, ""

    def _score_data_completeness(self, record: ArrestRecord) -> Tuple[int, str]:
        """Score based on data completeness."""
        required_fields = [
            ('Full_Name', record.Full_Name),
            ('Charges', record.Charges),
            ('Bond_Amount', record.Bond_Amount),
            ('Court_Date', record.Court_Date),
        ]

        missing_fields = [name for name, value in required_fields if not value or not value.strip()]

        if not missing_fields:
            return 15, "Complete data (all required fields present) (+15)"
        else:
            missing_str = ', '.join(missing_fields)
            return -10, f"Missing data: {missing_str} (-10)"

    def _check_disqualifying_charges(self, charges: str) -> Tuple[int, str]:
        """Check for disqualifying charges."""
        if not charges:
            return 0, ""

        charges_lower = charges.lower()

        for keyword in self.DISQUALIFYING_CHARGES:
            if keyword in charges_lower:
                return -100, f"DISQUALIFIED: Severe charge ({keyword}) (-100)"

        return 0, ""

    def _score_charge_severity(self, charges: str) -> Tuple[int, str]:
        """NLP-enhanced charge severity scoring using legal_nlp_service."""
        if not charges:
            return 0, ""

        try:
            from dashboard.services.legal_nlp_service import analyze_charges
            analysis = analyze_charges(charges)
            severity = analysis.get("max_severity", "unknown")
            level = analysis.get("severity_level", 0)

            # Higher severity = more serious case = bondable but needs attention
            if severity in ("felony_1", "capital"):
                return 15, f"NLP severity: {severity} (level {level}) — high-value case (+15)"
            elif severity == "felony_2":
                return 10, f"NLP severity: {severity} (level {level}) — substantial case (+10)"
            elif severity == "felony_3":
                return 5, f"NLP severity: {severity} (level {level}) — moderate case (+5)"
            elif severity in ("misdemeanor_1", "misdemeanor_2"):
                return 0, ""  # No bonus for misdemeanors
            return 0, ""
        except Exception:
            return 0, ""  # Fail silently — NLP is a bonus, not required

    def _score_fta_risk(self, charges: str) -> Tuple[int, str]:
        """Score FTA risk indicators from charge text."""
        if not charges:
            return 0, ""

        charges_lower = charges.lower()

        # Direct FTA indicators — these people are bondable but high flight risk
        # From the bond agency perspective, higher risk = opportunity but caution
        high_risk_kws = ['failure to appear', 'fta', 'fugitive', 'absconded',
                         'fleeing', 'bench warrant', 'capias']
        medium_risk_kws = ['violation of probation', 'vop', 'contempt',
                           'habitual offender']

        for kw in high_risk_kws:
            if kw in charges_lower:
                return 15, f"FTA risk: '{kw}' detected — high-risk bondable lead (+15)"

        for kw in medium_risk_kws:
            if kw in charges_lower:
                return 8, f"FTA risk: '{kw}' detected — medium-risk lead (+8)"

        return 0, ""

    def _determine_lead_status(self, score: int) -> str:
        """Determine lead status based on score."""
        if score < 0:
            return "Disqualified"
        elif score >= self.HOT_THRESHOLD:
            return "Hot"
        elif score >= self.WARM_THRESHOLD:
            return "Warm"
        else:
            return "Cold"

    def _parse_bond_amount(self, bond_amount: str) -> float:
        """Parse bond amount string to numeric value."""
        if not bond_amount or not bond_amount.strip():
            return None

        cleaned = bond_amount.strip().upper()

        if any(term in cleaned for term in ['NO BOND', 'NONE', 'N/A', 'HOLD']):
            return 0.0

        cleaned = re.sub(r'[$,\s]', '', cleaned)

        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return None

    def get_score_breakdown(self) -> List[str]:
        """Get the detailed breakdown of the last score calculation."""
        return self.score_breakdown.copy()

    def score_and_update(self, record: ArrestRecord) -> ArrestRecord:
        """Score an arrest record and update its Lead_Score and Lead_Status fields."""
        score, status = self.score_arrest(record)
        record.Lead_Score = score
        record.Lead_Status = status
        return record


# Convenience functions
def score_arrest(record: ArrestRecord) -> Tuple[int, str]:
    """Quick score an arrest record."""
    scorer = LeadScorer()
    return scorer.score_arrest(record)


def score_and_update(record: ArrestRecord) -> ArrestRecord:
    """Score and update an arrest record in-place."""
    scorer = LeadScorer()
    return scorer.score_and_update(record)

