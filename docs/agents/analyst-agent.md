# Lead Scoring Agent — "The Analyst"

> **Status:** `[IMPLEMENTED]`
> **Implementation:** `scoring/lead_scorer.py`

---

## Role

The Analyst evaluates every scraped arrest record and assigns a lead score (0–100) with a classification tier (Hot / Warm / Cold / Disqualified). This score determines alerting priority and downstream handling — hot leads get immediate Slack alerts and outreach queuing.

---

## Scoring Algorithm

```
ArrestRecord
    → Bond Amount scoring (+30 to -50)
    → Bond Type scoring (+25 to -50)
    → Custody Status scoring (+20 to -30)
    → Data Completeness scoring (+15 to -10)
    → Disqualifier check (-100 for capital/murder/federal)
    → Final score clamped to 0–100
    → Classification: Hot (≥80) / Warm (50–79) / Cold (30–49) / Disqualified (<30)
```

### Scoring Factors

| Factor | Signal | Points |
|--------|--------|--------|
| Bond Amount | $500–$50K | +30 |
| Bond Amount | $50K–$100K | +20 |
| Bond Amount | >$100K | +10 |
| Bond Amount | <$500 | -10 |
| Bond Amount | $0 | -50 |
| Bond Type | Cash/Surety | +25 |
| Bond Type | No Bond/Hold | -50 |
| Bond Type | ROR | -30 |
| Custody Status | In Custody | +20 |
| Custody Status | Released | -30 |
| Data Completeness | All required fields present | +15 |
| Data Completeness | Missing critical fields | -10 |
| Disqualifier | Capital/Murder/Federal charges | -100 |

### Classification Tiers

| Score | Tier | Action |
|-------|------|--------|
| 80+ | 🔥 **Hot** | Immediate Slack alert to `#leads`, queued for outreach |
| 50–79 | 🟡 **Warm** | Logged, available in Lead Explorer, low-priority follow-up |
| 30–49 | ❄️ **Cold** | Stored in MongoDB, no action taken |
| <30 | ⛔ **Disqualified** | Stored briefly (7-day retention), no action |

---

## Key Files

| File | Purpose |
|------|---------|
| `scoring/lead_scorer.py` | Scoring engine — `score_arrest(record) → (score, status)` |
| `scrapers/base_scraper.py` | Calls scorer in `run()` pipeline before write |
| `writers/slack_notifier.py` | Fires alert for Hot leads |
| `dashboard/sl-features.js` | Lead Explorer frontend (filter by score/tier) |

---

## Constraints

- **Score Everything** — No record enters the DB without a lead score (Prime Directive #3)
- Scoring runs synchronously in the scraper pipeline before MongoDB write
- Score and status are stored as `lead_score` (int) and `lead_status` (string) on the arrest document
- Scores are deterministic — same input always produces same output
- Tuning guide: `.agent/skills/lead-scoring-tuning/SKILL.md`
