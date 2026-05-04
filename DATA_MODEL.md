# DATA_MODEL.md

## Purpose
This file defines the canonical entities and identity rules used throughout ShamrockLeads.
Entities marked `[IMPLEMENTED]` exist in code. Entities marked `[PLANNED — Phase N]` are architectural targets.

> **Read `BRAND.md` first** — it defines who we are, what we're building, and the non-negotiable standards every agent must follow.
> **Last Updated:** 2026-05-04

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
- location event
- risk flag
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

## 2. BondedCase `[IMPLEMENTED — shamrock-bond-tracker]`
Represents a written bail bond. Created when an agent writes a bond.
Implemented in `shamrock-bond-tracker/tracker/models.py` as the `BondCase` dataclass.
Stored in MongoDB collection: `shamrock_tracker.bond_cases`

### Primary identity
- `bond_id` (UUID, generated at write time)
- Natural key: `County + Booking_Number` (links back to ArrestLead)

### Key fields
- `defendant_phone` — E.164 format; used to match inbound Twilio SMS to this bond
- `bond_status` — ACTIVE / FORFEITED / EXONERATED / SURRENDERED
- `risk_level` — LOW / MEDIUM / HIGH / CRITICAL (computed from location events)
- `last_known_city`, `last_known_state`, `last_known_lat`, `last_known_lon` — updated on each location event
- `location_event_count` — running total of captured location events

### Constraints
- One BondedCase per `bond_id`
- `defendant_phone` must be unique across ACTIVE bonds (one phone → one active bond)
- BondedCase is the only approved bridge between ArrestLead and location tracking

---

## 3. LocationEvent `[IMPLEMENTED — shamrock-bond-tracker]`
Represents a single IP geolocation capture from an inbound SMS.
Stored in MongoDB collection: `shamrock_tracker.location_events`

### Primary identity
- `event_id` (UUID)
- Foreign key: `bond_id` → BondedCase

### Key fields
- `ip_address` — extracted public IP from SMS body
- `city`, `region`, `country`, `lat`, `lon` — from MaxMind GeoLite2
- `isp`, `org` — ISP/organization from GeoLite2
- `is_vpn`, `is_proxy`, `is_tor` — from ipquery or heuristic detection
- `risk_score` — 0–100 computed by RiskEngine
- `risk_flags` — list of flag codes (e.g., `VPN_DETECTED`, `STATE_JUMP`, `TOR_DETECTED`)
- `distance_from_last_km` — geodesic distance from previous location event
- `source_platform` — `twilio_sms` / `whatsapp` / `manual`
- `message_sid` — Twilio message SID for audit trail

### Constraints
- Immutable after creation (append-only log)
- One LocationEvent per unique `ip_address` per `bond_id` per day (dedup)

---

## 4. RiskFlag `[IMPLEMENTED — shamrock-bond-tracker]`
Represents an auto-generated alert requiring agent review.
Stored in MongoDB collection: `shamrock_tracker.risk_flags`

### Primary identity
- `flag_id` (UUID)
- Foreign key: `bond_id` → BondedCase

### Key fields
- `flag_type` — e.g., `TOR_DETECTED`, `COUNTRY_JUMP`, `RAPID_MOVEMENT_CRITICAL`
- `severity` — MEDIUM / HIGH / CRITICAL
- `description` — human-readable explanation
- `acknowledged` — bool; set to true when agent reviews
- `acknowledged_by` — agent name
- `acknowledged_at` — timestamp

### Constraints
- One open flag per `bond_id + flag_type` (no duplicate open flags of same type)
- Flags are never deleted — acknowledged flags are retained for audit

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
- **location tracking** ← NEW
- **risk flag escalation** ← NEW

No shortcutting the chain.

---

## Cross-Repo Architecture

| Repo | Responsibility | MongoDB DB |
|---|---|---|
| `shamrock-leads` | Arrest scraping, lead scoring, Slack alerts | `shamrock_leads` |
| `shamrock-bond-tracker` | Bond written side, location tracking, risk flags | `shamrock_tracker` |
| `shamrock-bail-portal-site` | Indemnitor portal, GAS, SignNow, Drive | Wix CMS + Google Sheets |

The `County + Booking_Number` key is the canonical link between `shamrock-leads` (ArrestLead) and `shamrock-bond-tracker` (BondedCase).
