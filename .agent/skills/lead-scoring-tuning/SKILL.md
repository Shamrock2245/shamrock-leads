---
name: lead-scoring-tuning
description: Guide for adjusting lead scoring weights, adding new signals, and calibrating thresholds. Use when scoring accuracy needs improvement.
---

# Lead Scoring Tuning

> Adjust the scoring engine to maximize conversion rates.

## When to Use
- Hot leads are being generated that don't convert (false positives)
- Convertible leads are scored too low (false negatives)
- New data signals become available (e.g., prior arrests, employment)
- User says "adjust scoring", "too many hot leads", "missing good leads"
- A new county has different bond patterns that need calibration

---

## Current Scoring Model

**File:** `scoring/lead_scorer.py`

### Weights

| Factor | Current Weight | Range |
|--------|---------------|-------|
| Bond Amount ($500-$999) | +30 | 0-50 |
| Bond Amount ($1,000-$4,999) | +40 | 0-50 |
| Bond Amount ($5,000+) | +50 | 0-50 |
| Arrest Recency (<24h) | +20 | 0-20 |
| Arrest Recency (<48h) | +10 | 0-20 |
| Charge Severity (Felony) | +20 | 0-20 |
| Charge Severity (DUI/Battery) | +15 | 0-20 |
| FL Resident | +20 | 0-20 |
| Out of State | -30 | -30 to 0 |

### Thresholds

| Status | Score Range | Current |
|--------|-----------|---------|
| 🔥 Hot | ≥ 80 | 80 |
| 🟡 Warm | 50-79 | 50 |
| ❄️ Cold | 30-49 | 30 |
| ❌ Disqualified | < 30 OR trigger | 30 |

### Auto-Disqualifiers (Score → 0)

- `Status == "Released"` or `Status == "ROR"`
- `Bond_Amount == "$0.00"` or `Bond_Amount == "NO BOND"`
- `Bond_Status == "Posted"`
- Charges contain only: "FTA", "PROBATION VIOLATION" (low-profit)

---

## Tuning Procedure

### Step 1: Analyze Current Performance

```python
# Query MongoDB for conversion data
db.arrests.aggregate([
    {"$match": {"Lead_Status": "Hot"}},
    {"$group": {
        "_id": "$County",
        "total_hot": {"$sum": 1},
        "avg_score": {"$avg": "$Lead_Score"},
        "avg_bond": {"$avg": {"$toDouble": "$Bond_Amount"}}
    }}
])
```

### Step 2: Identify Mis-Scored Records

**False Positives** (scored Hot but not good leads):
```python
# Look for Hot leads with very low bond amounts
db.arrests.find({
    "Lead_Status": "Hot",
    "Bond_Amount": {"$lt": "$500"}
}).sort({"Lead_Score": -1}).limit(20)
```

**False Negatives** (scored low but actually good leads):
```python
# Look for Cold/Warm leads with high bond amounts
db.arrests.find({
    "Lead_Status": {"$in": ["Cold", "Warm"]},
    "Bond_Amount": {"$gte": "$5000"}
}).sort({"Lead_Score": 1}).limit(20)
```

### Step 3: Adjust Weights

Modify `scoring/lead_scorer.py`:

```python
# Example: Increase bond amount weight
SCORING_WEIGHTS = {
    "bond_high": 50,      # Was 40
    "bond_medium": 35,    # Was 30
    "recency_24h": 25,    # Was 20
    # ...
}
```

### Step 4: Backtest

```bash
# Re-score all existing records
python -c "
from scoring.lead_scorer import LeadScorer
from writers.mongo_writer import MongoWriter

scorer = LeadScorer()
writer = MongoWriter()
records = writer.get_all_records()

for r in records:
    old_score = r.Lead_Score
    scorer.score_and_update(r)
    if r.Lead_Status != old_status:
        print(f'{r.Booking_Number}: {old_score} → {r.Lead_Score} ({r.Lead_Status})')
"
```

### Step 5: Deploy & Monitor

- Push updated weights
- Monitor Slack for 24h
- Compare hot lead volume before/after
- Verify no increase in false positives

---

## Advanced Signals (Future)

| Signal | Weight | Source |
|--------|--------|--------|
| Prior arrests count | +5 per prior | MongoDB history |
| Same-county repeat | -10 | Flight risk indicator |
| Has local address | +15 | Address parsing |
| Multiple felonies | +10 | Charge analysis |
| Weekend arrest | +5 | Higher urgency |
| Holiday arrest | +10 | Offices closed |

---

## County-Specific Overrides

Some counties have different bond scales:

```python
COUNTY_ADJUSTMENTS = {
    "Miami-Dade": {"bond_multiplier": 0.8},  # Higher bonds by default
    "Rural": {"bond_multiplier": 1.2},        # Lower bonds, still valuable
}
```
