# Paperwork Agent

> **Status:** `[PLANNED — Phase 6]`
> **This agent does not exist in code yet.**

---

## Role

Generates surety-specific bond paperwork packets by selecting the correct SignNow template set based on the surety backing the bond case.

---

## Prerequisites

- Phase 5 complete (BondCase with Surety_ID and POA_Number assigned)

## Behavior

1. Receive bond case ID
2. Validate: defendant, indemnitor, match, surety, POA, case number all present
3. Select template set by `Surety_ID`:
   - OSI (O'Shaughnahill) → OSI template set
   - Palmetto → Palmetto template set
4. Copy template in SignNow
5. Hydrate fields from BondCase + Defendant + Indemnitor data
6. Create DocumentPacket record with `Packet_Version = 1`
7. Log generation as AuditEvent

## Policy

See `docs/policies/signature-policy.md` for packet binding rules.

## Constraints

- Packet must bind to exactly one BondCase
- Template must match surety
- POA prefix must match surety
- Never mutate a sent/signed packet — create new version
- All six identity fields required before generation
