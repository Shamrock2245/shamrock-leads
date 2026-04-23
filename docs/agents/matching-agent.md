# Matching Agent — "The Matcher"

> **Status:** `[PLANNED — Phase 4]`
> **This agent does not exist in code yet.**

---

## Role

The Matcher links an indemnitor intake record to the correct defendant record with confidence scoring and human validation gating.

---

## Prerequisites

- Phase 2 complete (Defendant records exist)
- Phase 3 complete (Indemnitor records exist)

## Behavior

1. Receive indemnitor intake (from GAS, Wix, Telegram, or voice)
2. Query defendant records by name, county, booking number, DOB
3. Score each candidate match (0.0–1.0)
4. Auto-validate if confidence ≥ 0.85 AND booking number matches exactly
5. Surface proposed matches (0.60–0.84) to human for review
6. Flag low-confidence matches (<0.60) for manual investigation
7. Log all match attempts as AuditEvents

## Policy

See `docs/policies/matching-policy.md` for full rules.

## Constraints

- No paperwork before validated match
- One match per intake
- No multi-defendant matching
- Rejected matches logged with reason
- Stale custody status invalidates match
