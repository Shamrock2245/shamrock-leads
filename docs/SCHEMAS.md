# SCHEMAS.md — ShamrockLeads Data Schemas

> **Last Updated:** May 8, 2026
> **Source of Truth:** `core/models.py` (ArrestRecord dataclass)

---

## MongoDB Collections

### `arrests` — Primary arrest data `[IMPLEMENTED]`

Populated by the scraper pipeline. Dedup key: `county + booking_number`.

### Document Schema (39 fields + metadata)

| MongoDB Field | Python Field | Type | Description |
|---------------|-------------|------|-------------|
| `scrape_timestamp` | `Scrape_Timestamp` | string | ISO 8601 UTC timestamp of scrape |
| `county` | `County` | string | **PARTITION KEY** — Florida county name |
| `booking_number` | `Booking_Number` | string | **DEDUP KEY** (with county) |
| `person_id` | `Person_ID` | string | Jail-assigned person identifier |
| `full_name` | `Full_Name` | string | Combined name |
| `first_name` | `First_Name` | string | Defendant first name |
| `middle_name` | `Middle_Name` | string | Middle name or initial |
| `last_name` | `Last_Name` | string | Defendant last name |
| `dob` | `DOB` | string | Date of birth |
| `arrest_date` | `Arrest_Date` | string | Date of arrest |
| `arrest_time` | `Arrest_Time` | string | Time of arrest |
| `booking_date` | `Booking_Date` | string | Date booked into jail |
| `booking_time` | `Booking_Time` | string | Time booked |
| `status` | `Status` | string | `In Custody`, `Released`, `Transferred` |
| `facility` | `Facility` | string | Jail/facility name |
| `agency` | `Agency` | string | Arresting agency |
| `race` | `Race` | string | As reported |
| `sex` | `Sex` | string | M/F (uppercased, single char) |
| `height` | `Height` | string | e.g., `5'10"` |
| `weight` | `Weight` | string | In pounds |
| `address` | `Address` | string | Street address if available |
| `city` | `City` | string | City |
| `state` | `State` | string | Two-letter abbreviation (default: FL) |
| `zip` | `ZIP` | string | ZIP code |
| `mugshot_url` | `Mugshot_URL` | string | Direct URL to mugshot |
| `charges` | `Charges` | string | Semicolon-delimited charge descriptions |
| `bond_amount` | — | float | Parsed numeric bond amount |
| `bond_amount_raw` | `Bond_Amount` | string | Raw bond string (e.g., `$5,000.00`, `NO BOND`) |
| `bond_paid` | `Bond_Paid` | string | `YES` or `NO` |
| `bond_type` | `Bond_Type` | string | `Surety`, `Cash`, `ROR`, etc. |
| `court_type` | `Court_Type` | string | `Arraignment`, `Hearing`, `Trial` |
| `case_number` | `Case_Number` | string | Court case number |
| `court_date` | `Court_Date` | string | Next scheduled court date |
| `court_time` | `Court_Time` | string | Court time |
| `court_location` | `Court_Location` | string | Courtroom/location |
| `detail_url` | `Detail_URL` | string | URL to detailed booking page |
| `lead_score` | `Lead_Score` | int | 0–100 (set by LeadScorer) |
| `lead_status` | `Lead_Status` | string | `Hot`, `Warm`, `Cold`, `Disqualified` |
| `last_checked` | `LastChecked` | string | Last status check timestamp |
| `last_checked_mode` | `LastCheckedMode` | string | Check method identifier |
| `updated_at` | — | datetime | MongoDB update timestamp |
| `extra` | `extra_data` | object | Additional county-specific data |

### Indexes

- Unique compound: `county + booking_number`
- Index: `lead_score` (for hot lead queries)
- Index: `lead_status` (for filtering)
- Index: `county` (for per-county queries)
- Index: `updated_at` (for recency)

---

### `leads` — Scored leads view `[IMPLEMENTED]`

Contains the same documents as `arrests` but filtered/indexed for lead-focused queries. In practice, the scraper writes to `arrests` and the dashboard reads from the same collection with score-based filtering.

---

### `poa_inventory` — POA number tracking `[IMPLEMENTED]`

See `docs/specs/surety-config-schema.md` for full schema.

---

### `bond_cases` — Active bond cases `[IMPLEMENTED]`

See `docs/specs/bond-case-schema.md` for full schema. Production collection name: `active_bonds`.

---

### `defendants` — Normalized person records `[IMPLEMENTED]`

See `DATA_MODEL.md` Section 2 for schema.

---

### `indemnitors` — Co-signer records `[IMPLEMENTED]`

See `DATA_MODEL.md` Section 3 for schema.

---

### `matches` — Defendant-indemnitor links `[IMPLEMENTED]`

See `DATA_MODEL.md` Section 4 for schema.

---

### `audit_events` — Immutable event log `[IMPLEMENTED]`

See `DATA_MODEL.md` Section 10 for schema.

---

## Google Sheets (Legacy)

### Sheet Structure

One tab per county. Header row matches the 39-column canonical schema from `ArrestRecord.get_header_row()`.

### Column Order (for Sheets compatibility)

```
Scrape_Timestamp, County, Booking_Number, Person_ID, Full_Name,
First_Name, Middle_Name, Last_Name, DOB, Arrest_Date, Arrest_Time,
Booking_Date, Booking_Time, Status, Facility, Agency,
Race, Sex, Height, Weight, Address, City, State, ZIP,
Mugshot_URL, Charges, Bond_Amount, Bond_Paid, Bond_Type,
Court_Type, Case_Number, Court_Date, Court_Time, Court_Location,
Detail_URL, Lead_Score, Lead_Status, LastChecked, LastCheckedMode
```

---

## Surety Configuration (Code)

Surety business rules are encoded in `core/models.py` as `SuretyConfig` dataclasses.

| Surety | ID | Tiers | Premium Split |
|--------|-----|-------|---------------|
| O'Shaughnahill Surety & Insurance, Inc. | `osi` | OSI3, OSI6, OSI16, OSI51, OSI101, OSI251 | $7.50/$5.00 per $100 |
| Palmetto Surety Corporation | `palmetto` | PSC5, PSC15, PSC25, PSC50, PSC75, PSC105, PSC200, PSC250 | $10.00/$5.00 per $100 |

Usage:
```python
from core.models import get_surety

osi = get_surety("osi")
split = osi.calculate_split(bond_amount=10000)
# {'premium': 1000.0, 'surety_owed': 75.0, 'buf_owed': 50.0, 'agent_retains': 875.0}

tier = osi.get_tier_for_bond(5000)
# POATier(prefix='OSI6', max_bond_value=6000)
```
