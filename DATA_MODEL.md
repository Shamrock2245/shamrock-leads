# DATA_MODEL.md — ShamrockLeads Entity & Schema Reference

> **Last Updated:** 2026-06-11
> **Database:** MongoDB Atlas — `ShamrockBailDB`
> **Dedup Key:** `county` + `booking_number` (for arrests)
> **Identity Rule:** ArrestLead ≠ Defendant ≠ Indemnitor ≠ Match ≠ BondCase. Never collapse.

---

## Collections Overview

| Collection | Purpose | Dedup Key |
|------------|---------|-----------|
| `arrests` | Raw scraped arrest records from 100 FL/GA counties (39 fields) | `county` + `booking_number` |
| `defendants` | Normalized defendant profiles | `Defendant_ID` (UUID) |
| `indemnitors` | Indemnitor intake records | `Indemnitor_ID` (UUID) |
| `matches` | Validated defendant↔indemnitor links | `Match_ID` (UUID) |
| `active_bonds` | Bonded cases with 7-status lifecycle | `Bond_Case_ID` (UUID), natural: `poa_number` + `case_number` |
| `prospective_bonds` | Pre-bond pipeline (leads being worked) | `_id` (ObjectId) |
| `poa_inventory` | Power of Attorney inventory per surety | `poa_number` (unique) |
| `paperwork_packets` | SignNow document packet metadata | `Packet_ID` (UUID) |
| `payments` | Payment log (SwipeSimple) | `Payment_ID` (UUID) |
| `payment_plans` | Scheduled payment plans | `_id` (ObjectId) |
| `intake_queue` | Incoming intake submissions | `_id` (ObjectId) |
| `audit_events` | Immutable state change log | `Event_ID` (UUID) |
| `defendant_notes` | Free-text notes on defendants | `_id` (ObjectId) |
| `court_reminders` | Scheduled SMS court reminders | `_id` (ObjectId) |
| `notifications` | Dashboard notification center | `_id` (ObjectId) |
| `outreach_sequences` | Drip campaign state machine | `_id` (ObjectId) |

---

## ArrestRecord (39 Fields)

The canonical arrest record. Written by every county scraper, scored by `lead_scorer.py`, stored in `arrests`.

```
full_name             : str       # "LAST, FIRST MIDDLE" or "FIRST LAST"
first_name            : str
middle_name           : str
last_name             : str
date_of_birth         : str       # ISO "YYYY-MM-DD"
age                   : int
race                  : str       # W/B/H/A/O
sex                   : str       # M/F
height                : str
weight                : str
hair_color            : str
eye_color             : str
address               : str
city                  : str
state                 : str
zip_code              : str
booking_number        : str       # DEDUP KEY (+ county)
case_number           : str
arrest_date           : str       # ISO
booking_date          : str       # ISO
release_date          : str       # ISO or None
facility              : str
charges               : list[dict]  # [{description, degree, bond_amount, bond_type, statute}]
total_bond_amount     : float
bond_type             : str       # "SURETY", "CASH", "ROR", "NO BOND"
custody_status        : str       # "In Custody", "Released"
court_date            : str       # ISO or None
court_type            : str
judge                 : str
arresting_agency      : str
mugshot_url           : str
source_url            : str
county                : str       # DEDUP KEY (+ booking_number)
scraped_at            : str       # ISO timestamp
lead_score            : int       # 0–100
lead_status           : str       # "hot", "warm", "cold", "disqualified"
notes                 : str
image_url             : str
raw_html              : str       # Optional debug field
```

### Required Fields (minimum viable record)
- `full_name` (or `first_name` + `last_name`)
- `booking_number`
- `county`
- `scraped_at`

---

## Defendant

Normalized, deduplicated defendant profile linked to one or more arrest records.

```
Defendant_ID          : str (UUID)
full_name             : str
first_name            : str
last_name             : str
date_of_birth         : str
phone                 : str
email                 : str
address               : str
linked_arrests        : list[ObjectId]   # refs to arrests._id
notes                 : list[dict]       # [{text, author, created_at}]
created_at            : str (ISO)
updated_at            : str (ISO)
```

---

## Indemnitor

Person responsible for the bond (cosigner).

```
Indemnitor_ID         : str (UUID)
full_name             : str
first_name            : str
last_name             : str
phone                 : str
email                 : str
address               : str
relationship          : str              # "Mother", "Spouse", "Friend", etc.
source                : str              # "wix", "telegram", "walk-in", "phone"
created_at            : str (ISO)
```

---

## Match

Validated link between an Indemnitor and a Defendant.

```
Match_ID              : str (UUID)
indemnitor_id         : str (ref)
defendant_id          : str (ref)
arrest_id             : str (ref)
confidence            : float            # 0.0–1.0
strategy              : str              # "exact_booking", "fuzzy_name_dob", "county_name", "manual"
status                : str              # "pending", "confirmed", "rejected"
confirmed_by          : str              # "system" or staff name
created_at            : str (ISO)
confirmed_at          : str (ISO)
```

---

## Active Bond (7-Status Lifecycle)

A bonded case with surety, POA, and court tracking.

```
Bond_Case_ID          : str (UUID)
defendant_id          : str (ref)
indemnitor_id         : str (ref)
match_id              : str (ref)
arrest_id             : str (ref)

# Bond Details
poa_number            : str              # From POA inventory
case_number           : str
court_case_number     : str
surety_id             : str              # "osi" or "palmetto"
bond_amount           : float
premium_amount        : float
premium_rate          : float            # Typically 0.10 (10%)

# Status
status                : str              # "active" | "monitoring" | "alert" | "exonerated" | "forfeited" | "surrendered" | "reinstated"
status_history        : list[dict]       # [{from_status, to_status, changed_by, changed_at, note}]

# Court
court_date            : str (ISO)
court_location        : str
judge                 : str

# Parties
defendant_name        : str
defendant_phone       : str
indemnitor_name       : str
indemnitor_phone      : str

# Paperwork
packet_id             : str (ref)
signing_status        : str              # "pending", "sent", "completed"
signnow_document_id   : str

# Timestamps
created_at            : str (ISO)
updated_at            : str (ISO)
posted_date           : str (ISO)
```

---

## POA Inventory

Power of Attorney documents tracked per surety.

```
poa_number            : str (unique)
surety_id             : str              # "osi" or "palmetto"
status                : str              # "available", "assigned", "void", "used"
tier                  : str              # bond amount tier
assigned_to           : str (ref)        # Bond_Case_ID when assigned
assigned_at           : str (ISO)
released_at           : str (ISO)        # Set when bond exonerated/forfeited/surrendered
book_number           : str
created_at            : str (ISO)
```

---

## Audit Event

Immutable record of every state change. **Never update or delete.**

```
Event_ID              : str (UUID)
entity_type           : str              # "bond", "defendant", "intake", "poa", etc.
entity_id             : str
action                : str              # "status_change", "poa_assigned", "match_confirmed", etc.
actor                 : str              # "system", "staff:brendan", "agent:matcher"
old_value             : any
new_value             : any
metadata              : dict
created_at            : str (ISO)
```

---

## Prospective Bond

Pre-bond pipeline record — a lead being actively worked.

```
defendant_id          : str (ref)
indemnitor_id         : str (ref)
arrest_id             : str (ref)
status                : str              # "new", "contacted", "intake_pending", "intake_complete", "matching", "ready_to_bond"
bond_amount           : float
county                : str
source                : str
notes                 : str
created_at            : str (ISO)
updated_at            : str (ISO)
```

---

## Key Indexes

| Collection | Index | Type | Purpose |
|------------|-------|------|---------|
| `arrests` | `{county: 1, booking_number: 1}` | Unique | Dedup key |
| `arrests` | `{lead_status: 1, scraped_at: -1}` | Compound | Hot lead queries |
| `arrests` | `{county: 1, scraped_at: -1}` | Compound | Per-county dashboard |
| `active_bonds` | `{poa_number: 1}` | Unique (sparse) | POA lookup |
| `active_bonds` | `{status: 1}` | Single | Kanban grouping |
| `poa_inventory` | `{poa_number: 1}` | Unique | POA lookup |
| `poa_inventory` | `{surety_id: 1, status: 1}` | Compound | Available POA queries |
| `audit_events` | `{created_at: 1}` | TTL (90 days) | Auto-purge old events |

---

## Data Flow Rules

1. **Scraping → `arrests`**: Upsert by `county` + `booking_number`. Never overwrite with older data.
2. **`arrests` → `defendants`**: Normalization creates/updates defendant profiles. Many arrests → one defendant.
3. **Intake → `indemnitors`**: Validated intake creates indemnitor record.
4. **`defendants` + `indemnitors` → `matches`**: Matching engine scores and links. Human gate for < 0.85 confidence.
5. **Confirmed match → `active_bonds`**: Only after: defendant exists, indemnitor exists, match confirmed, surety selected, POA assigned.
6. **`active_bonds` → `paperwork_packets`**: Packet generation hydrates SignNow templates with bond data.
7. **Status changes**: Every transition writes to `status_history[]` AND `audit_events`.
8. **POA lifecycle**: Available → Assigned (on bond creation) → Released (on exoneration/forfeit/surrender) → Available.
