# Audit Agent — "The Auditor"

> **Status:** `[IMPLEMENTED]`
> **Implementation:** `dashboard/api/events.py`, `dashboard/api/bond_lifecycle.py`
> **All state changes are logged to the `audit_events` MongoDB collection.**

---

## Role

Creates immutable audit events for every meaningful state change across all entities. The audit trail is the compliance backbone — it answers "who did what, when, and why" for any record.

---

## Prerequisites

- Phase 2 (should be built alongside Defendant normalization)

## Behavior

Every state change generates an AuditEvent with:
- `Event_ID` (immutable UUID)
- `Entity_Type` (arrest_lead, defendant, match, bond_case, etc.)
- `Entity_ID` (the affected record)
- `Action` (created, updated, validated, voided, etc.)
- `Old_State` → `New_State` (before/after snapshots)
- `Actor_Type` (system, agent, human)
- `Actor_Name` (which agent or user)
- `Reason` (why this happened)
- `Confidence` (if applicable)
- `Timestamp` (immutable)

## Storage

- `audit_events` collection in MongoDB
- **Never deleted. Never mutated.**
- Append-only log

## What Gets Audited

| Entity | Audited Actions |
|--------|----------------|
| ArrestLead | created, updated, score_changed |
| Defendant | created, updated, merged, deactivated |
| Indemnitor | created, updated, verified, deactivated |
| Match | proposed, validated, rejected |
| BondCase | created, surety_selected, poa_assigned, posted, discharged, forfeited, voided |
| DocumentPacket | generated, sent, signed, voided, regenerated |
| PaymentRequest | created, sent, paid, failed, refunded |
| POAInventory | received, assigned, used, voided, reported |

## Constraints

- Audit events are immutable — no updates, no deletes
- Every entity state transition must create an event
- Events must include enough context to reconstruct the timeline
- PII in audit events follows the same minimization rules as everywhere else
