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

Represents scraped booking/arrest information. This is the entry point for all data.
Implemented in `core/models.py` as the `ArrestRecord` dataclass.

### Primary identity
- `ArrestLead_ID` (MongoDB `_id`)
- Unique natural key: `County + Booking_Number`

### Fields (39-column canonical schema)
| Field | Type | Description |
|-------|------|-------------|
| `Scrape_Timestamp` | string | ISO 8601 UTC timestamp of scrape |
| `County` | string | **PARTITION KEY** — Florida county name |
| `Booking_Number` | string | **DEDUP KEY** (with County) |
| `Person_ID` | string | Jail-assigned person identifier |
| `Full_Name` | string | Combined name |
| `First_Name` | string | Defendant first name |
| `Middle_Name` | string | Middle name or initial |
| `Last_Name` | string | Defendant last name |
| `DOB` | string | Date of birth |
| `Arrest_Date` | string | Date of arrest |
| `Arrest_Time` | string | Time of arrest |
| `Booking_Date` | string | Date booked into jail |
| `Booking_Time` | string | Time booked |
| `Status` | string | `In Custody`, `Released`, `Transferred` |
| `Facility` | string | Jail/facility name |
| `Agency` | string | Arresting agency |
| `Race` | string | As reported |
| `Sex` | string | M/F |
| `Height` | string | e.g., `5'10"` |
| `Weight` | string | In pounds |
| `Address` | string | Street address if available |
| `City` | string | City |
| `State` | string | Two-letter abbreviation (default: FL) |
| `ZIP` | string | ZIP code |
| `Mugshot_URL` | string | Direct URL to mugshot |
| `Charges` | string | Semicolon-delimited charge descriptions |
| `Bond_Amount` | string | e.g., `$5,000.00` or `NO BOND` |
| `Bond_Paid` | string | `YES` or `NO` |
| `Bond_Type` | string | `Surety`, `Cash`, `ROR`, etc. |
| `Court_Type` | string | `Arraignment`, `Hearing`, `Trial` |
| `Case_Number` | string | Court case number |
| `Court_Date` | string | Next scheduled court date |
| `Court_Time` | string | Court time |
| `Court_Location` | string | Courtroom/location |
| `Detail_URL` | string | URL to detailed booking page |
| `Lead_Score` | int | 0–100 (set by LeadScorer) |
| `Lead_Status` | string | `Hot`, `Warm`, `Cold`, `Disqualified` |
| `LastChecked` | string | Last status check timestamp |
| `LastCheckedMode` | string | Check method identifier |

### Constraints
- One arrest lead per `County + Booking_Number`
- Updates may enrich existing record (upsert)
- Arrest lead is **not sufficient** to generate paperwork

---

## 2. Defendant `[PLANNED — Phase 2]`

Represents the person arrested, normalized across sources and bookings.

### Primary identity
- `Defendant_ID` (internal UUID)

### Linkages
- Can have one or more `ArrestLead_ID`s over time (re-arrests)
- Can have zero or more `Bond_Case_ID`s over time

### Fields
| Field | Type | Description |
|-------|------|-------------|
| `Defendant_ID` | string | Internal UUID |
| `Full_Legal_Name` | string | Canonical legal name |
| `First_Name` | string | |
| `Middle_Name` | string | |
| `Last_Name` | string | |
| `DOB` | string | |
| `Address` | string | |
| `Race` | string | |
| `Sex` | string | |
| `Height` | string | |
| `Weight` | string | |
| `Mugshot_URL` | string | Most recent |
| `Status` | string | Active / inactive |
| `Created_At` | datetime | |
| `Updated_At` | datetime | |

---

## 3. Indemnitor `[PLANNED — Phase 3]`

Represents the financially responsible person providing intake details and signing.

### Primary identity
- `Indemnitor_ID` (internal UUID)

### Fields
| Field | Type | Description |
|-------|------|-------------|
| `Indemnitor_ID` | string | Internal UUID |
| `Full_Legal_Name` | string | |
| `Phone` | string | Primary contact |
| `Email` | string | |
| `DOB` | string | If collected |
| `Address` | string | |
| `Relationship_To_Defendant` | string | |
| `Government_ID_Status` | string | `verified`, `pending`, `not_submitted` |
| `Intake_Source` | string | `wix`, `telegram`, `twilio`, `elevenlabs`, etc. |
| `Consent_Status` | string | |
| `Verification_Status` | string | |

---

## 4. Match `[PLANNED — Phase 4]`

Represents a proposed or approved link between one defendant and one indemnitor.

### Primary identity
- `Match_ID` (internal UUID)

### Fields
| Field | Type | Description |
|-------|------|-------------|
| `Match_ID` | string | |
| `Defendant_ID` | string | FK |
| `Indemnitor_ID` | string | FK |
| `Match_Status` | string | `proposed`, `validated`, `rejected`, `needs_review` |
| `Match_Confidence` | float | 0.0–1.0 |
| `Matched_On` | datetime | |
| `Validated_By` | string | Agent or human name |
| `Validation_Method` | string | |
| `Notes` | string | |

### Constraints
- No paperwork may begin until `Match_Status = validated`
- Low-confidence matches must not auto-advance

---

## 5. Surety `[PLANNED — Phase 5]`

Represents an insurance/surety company that Shamrock writes bonds under.

### Known Sureties

| ID | Company | States | POA Prefixes (by bond tier) |
|----|---------|--------|----------------------------|
| `osi` | Old Southern Indemnity | FL | `OSI3`, `OSI6`, `OSI51`, `OSI101`, `OSI251` |
| `palmetto` | Palmetto Surety Corporation | FL, SC, NC, TN, TX, CT, LA, MS | `PSC5`, `PSC15`, `PSC25`, `PSC50`, `PSC75`, `PSC101`, `PSC200`, `PSC250` |

### Fields
| Field | Type | Description |
|-------|------|-------------|
| `Surety_ID` | string | `osi` or `palmetto` |
| `Company_Name` | string | Full legal name |
| `Licensed_States` | list[string] | State abbreviations |
| `POA_Prefixes` | list[string] | Valid POA prefix codes |
| `Template_Set_ID` | string | SignNow template group |
| `Commission_Rate` | float | Agent commission % |
| `Minimum_Premium` | float | Minimum dollar premium |
| `Build_Up_Fund_Rate` | float | BUF percentage |
| `Active` | boolean | Currently in use |

---

## 6. POAInventory `[PLANNED — Phase 5]`

Tracks Power of Attorney numbers as physical assets with lifecycle management.
POA numbers come from physical books issued by each surety. Each prefix corresponds to a bond amount tier.

### Inventory Ingestion
POA inventory is tracked by scanning the inventory receipt received from each surety company. OCR or manual entry populates this collection.

### Primary identity
- `POA_ID` (internal UUID)
- `POA_Number` (unique — the actual printed number)

### Fields
| Field | Type | Description |
|-------|------|-------------|
| `POA_ID` | string | Internal UUID |
| `POA_Number` | string | The printed POA number (e.g., `PSC50-12345`) |
| `POA_Prefix` | string | Tier prefix (e.g., `PSC50`, `OSI101`) |
| `Surety_ID` | string | `osi` or `palmetto` |
| `Book_Number` | string | Physical book identifier |
| `Status` | string | `available`, `assigned`, `used`, `voided`, `reported` |
| `Bond_Case_ID` | string | Null until used, then FK to BondCase |
| `Assigned_To_Agent` | string | Which agent has this book |
| `Received_At` | datetime | When inventory receipt was scanned |
| `Used_At` | datetime | When POA was consumed |
| `Voided_At` | datetime | If voided |
| `Void_Reason` | string | |
| `Reported_At` | datetime | Monthly production report date |

### Lifecycle
```
Available → Assigned → Used → Reported
                  ↘ Voided → Reported
```

### Constraints
- `POA_Number` must be globally unique
- A `used` POA must link to exactly one `Bond_Case_ID`
- A `voided` POA never returns to `available`
- Monthly reconciliation: all `used` + `voided` POAs must be reported to the surety

---

## 7. BondCase `[PLANNED — Phase 5]`

Represents the actual operational bail bond workflow. Created only after a validated match.

### Primary identity
- `Bond_Case_ID` (internal UUID)

### Required identifiers
- `Defendant_ID`
- `Indemnitor_ID`
- `Match_ID`
- `Case_Number`
- `POA_Number`
- `Surety_ID`

### Fields
| Field | Type | Description |
|-------|------|-------------|
| `Bond_Case_ID` | string | Internal UUID |
| `Defendant_ID` | string | FK |
| `Indemnitor_ID` | string | FK |
| `Match_ID` | string | FK |
| `Surety_ID` | string | `osi` or `palmetto` |
| `County` | string | |
| `Booking_Number` | string | |
| `Case_Number` | string | Court case number |
| `POA_Number` | string | From POAInventory |
| `Bond_Amount` | float | |
| `Premium_Amount` | float | Typically 10% in FL |
| `Bond_Type` | string | |
| `Bond_Status` | string | `open`, `posted`, `discharged`, `forfeited`, `voided` |
| `Packet_Status` | string | `not_generated`, `generated`, `sent`, `signed`, `voided` |
| `Signature_Status` | string | `not_sent`, `sent`, `viewed`, `signed`, `declined` |
| `Payment_Status` | string | `not_requested`, `sent`, `partial`, `paid`, `delinquent` |
| `Created_By` | string | Agent or system |
| `Created_At` | datetime | |
| `Updated_At` | datetime | |

### Constraints
- `Bond_Case_ID` unique
- `POA_Number` unique among active cases
- `POA_Number + Case_Number` unique — must identify exactly one bonded case
- Packet generation must bind to `Bond_Case_ID`, never to raw arrest lead
- Active bonded case must reference exactly one validated defendant and one validated indemnitor
- `Surety_ID` must match the surety of the assigned `POA_Number`

### Preconditions for Creation
A BondCase must not be created unless:
- `Defendant_ID` exists
- `Indemnitor_ID` exists
- `Match_ID` is validated
- `Surety_ID` is specified

---

## 8. DocumentPacket `[PLANNED — Phase 6]`

Represents a generated paperwork packet bound to a specific surety's template set.

### Primary identity
- `Packet_ID` (internal UUID)

### Fields
| Field | Type | Description |
|-------|------|-------------|
| `Packet_ID` | string | |
| `Bond_Case_ID` | string | FK |
| `Surety_ID` | string | Determines template set |
| `Packet_Version` | int | Incrementing version |
| `Template_Set` | string | SignNow template group ID |
| `SignNow_Entity_ID` | string | SignNow document ID |
| `Document_Status` | string | `generated`, `sent`, `signed`, `voided` |
| `Generated_At` | datetime | |
| `Sent_At` | datetime | |
| `Signed_At` | datetime | |
| `Voided_At` | datetime | |

### Constraints
- Only one active packet version per bonded case
- Regenerate instead of mutate signed packet
- Template set must match the bonded case's surety

---

## 9. PaymentRequest `[PLANNED — Phase 8]`

### Primary identity
- `Payment_Request_ID` (internal UUID)

### Fields
| Field | Type | Description |
|-------|------|-------------|
| `Payment_Request_ID` | string | |
| `Bond_Case_ID` | string | FK |
| `Recipient_Indemnitor_ID` | string | FK |
| `Amount` | float | |
| `Currency` | string | `USD` |
| `Payment_Link` | string | SwipeSimple link |
| `Payment_Status` | string | `pending`, `sent`, `paid`, `failed`, `refunded` |
| `Sent_At` | datetime | |
| `Paid_At` | datetime | |
| `Failed_At` | datetime | |

### Constraints
- Payment recipient must match indemnitor on bonded case
- Payment status must not be inferred from messaging delivery alone

---

## 10. AuditEvent `[PLANNED — Phase 2+]`

Immutable log of state changes. Never deleted, never mutated.

### Primary identity
- `Event_ID` (immutable UUID)

### Fields
| Field | Type | Description |
|-------|------|-------------|
| `Event_ID` | string | UUID |
| `Entity_Type` | string | `arrest_lead`, `defendant`, `match`, `bond_case`, etc. |
| `Entity_ID` | string | The affected record's ID |
| `Action` | string | e.g., `created`, `updated`, `validated`, `voided` |
| `Old_State` | object | Previous state snapshot |
| `New_State` | object | New state snapshot |
| `Actor_Type` | string | `system`, `agent`, `human` |
| `Actor_Name` | string | Which agent or user |
| `Reason` | string | Why this action was taken |
| `Confidence` | float | If applicable |
| `Timestamp` | datetime | Immutable |

---

## State Transition Rule

The bonded-case record is the **only approved bridge** between:
- matched intake
- surety selection
- POA assignment
- case number confirmation
- paperwork generation
- signature workflow
- payment workflow

**No shortcutting the chain.**
