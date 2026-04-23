# Matching Policy

> **Status:** `[ACTIVE — Enforced from Phase 4]`

---

## Purpose

This policy governs how indemnitor intake records are matched to defendant records. Matching is the critical gate between "lead" and "bonded case" — no shortcuts allowed.

---

## Core Rule

**No bond paperwork may be generated until a match is validated.**

---

## Matching Process

### Step 1: Candidate Identification

When an indemnitor intake arrives, identify candidate defendants using:
- Defendant name (fuzzy match on first + last name)
- County of arrest
- Booking number (if provided by indemnitor)
- Date of arrest (if provided)
- Charges description (if provided)

### Step 2: Confidence Scoring

Each candidate gets a confidence score (0.0–1.0):

| Signal | Weight |
|--------|--------|
| Exact booking number match | 0.5 |
| Exact name match (first + last) | 0.3 |
| Fuzzy name match (>85% similarity) | 0.2 |
| County match | 0.1 |
| DOB match | 0.2 |
| Arrest date within 48h of intake | 0.1 |

### Step 3: Classification

| Confidence | Status | Action |
|------------|--------|--------|
| ≥ 0.85 | `validated` | Auto-approve (if booking number matched) |
| 0.60–0.84 | `proposed` | Surface to human for review |
| < 0.60 | `needs_review` | Flag for manual investigation |

### Step 4: Human Validation

- All `proposed` and `needs_review` matches require human confirmation
- Human marks match as `validated` or `rejected`
- Rejected matches are logged with reason

---

## Constraints

1. **One match per intake**: An indemnitor intake resolves to exactly one defendant. If ambiguous, escalate.
2. **No multi-defendant matching**: If an indemnitor wants to bond out multiple people, each requires a separate intake → match → bond case.
3. **No stale matches**: If the defendant's custody status changes to `Released` after match but before bond posting, the match must be re-validated.
4. **Audit trail**: Every match attempt (success, failure, rejection) creates an AuditEvent.

---

## Escalation Conditions

Escalate immediately if:
- Two or more defendants match with confidence > 0.60
- Indemnitor provides conflicting information (name says X, booking number resolves to Y)
- Defendant has multiple active bookings across counties
- Match was previously rejected and indemnitor is retrying
