# DATA_MODEL.md

## Purpose
This file defines the canonical entities and identity rules used throughout ShamrockLeads.
Entities marked `[IMPLEMENTED]` exist in code. Entities marked `[PLANNED — Phase N]` are architectural targets.

---

## Core Principle
Every workflow step must attach to the correct record boundary.

Do not collapse these concepts into one record:
- arrest lead
- defendant
- indemnitor
- match
- bonded case
- surety
- POA inventory
- document packet
- payment request
- audit event

---

## 1. ArrestLead `[IMPLEMENTED]`
Represents scraped booking/arrest information. Entry point for all data.
Implemented in `core/models.py` as the `ArrestRecord` dataclass.

### Primary identity
- `ArrestLead_ID` (MongoDB `_id`)
- Unique natural key: `County + Booking_Number`

### Constraints
- One arrest lead per `County + Booking_Number`
- Updates may enrich existing record (upsert)
- Arrest lead is NOT sufficient to generate paperwork

---

## State Transition Rule
The bonded-case record is the only approved bridge between:
- matched intake
- surety selection
- POA assignment
- case number confirmation
- paperwork generation
- signature workflow
- payment workflow

No shortcutting the chain.
