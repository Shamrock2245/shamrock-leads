# 📊 ShamrockLeads — Data Schemas

> Single source of truth for all data structures in the system.

---

## ArrestRecord (39 Columns)

The core data model. Every arrest scraped from any county normalizes into this schema.

| # | Field | Type | Required | Description |
|---|-------|------|----------|-------------|
| 1 | `First_Name` | string | ✅ | Defendant first name (title case) |
| 2 | `Middle_Name` | string | | Middle name or initial |
| 3 | `Last_Name` | string | ✅ | Defendant last name (title case) |
| 4 | `Date_of_Birth` | string | | Format: `MM/DD/YYYY` |
| 5 | `Gender` | string | | `Male`, `Female`, `Unknown` |
| 6 | `Race` | string | | As reported by jail roster |
| 7 | `Height` | string | | Format: `5'10"` |
| 8 | `Weight` | string | | In pounds |
| 9 | `Hair_Color` | string | | |
| 10 | `Eye_Color` | string | | |
| 11 | `Address` | string | | Street address if available |
| 12 | `City` | string | | |
| 13 | `State` | string | | Two-letter abbreviation |
| 14 | `Zip` | string | | |
| 15 | `Booking_Number` | string | ✅ | **PRIMARY KEY** (with County) |
| 16 | `Booking_Date` | string | ✅ | Format: `MM/DD/YYYY` |
| 17 | `Booking_Time` | string | | Format: `HH:MM` (24h) |
| 18 | `Release_Date` | string | | Set when inmate is released |
| 19 | `Facility` | string | | Jail/facility name |
| 20 | `County` | string | ✅ | **PARTITION KEY** — one of 67 FL counties |
| 21 | `Charges` | string | ✅ | Semicolon-delimited charge descriptions |
| 22 | `Charge_Statutes` | string | | FL statute numbers |
| 23 | `Charge_Levels` | string | | `F` (Felony), `M` (Misdemeanor), `O` (Ordinance) |
| 24 | `Bond_Amount` | string | | Format: `$5,000.00` or `NO BOND` |
| 25 | `Bond_Status` | string | | `Posted`, `Not Posted`, `ROR`, `Hold` |
| 26 | `Bond_Type` | string | | `Surety`, `Cash`, `ROR`, `None` |
| 27 | `Court_Date` | string | | Next scheduled court date |
| 28 | `Court_Room` | string | | Courtroom identifier |
| 29 | `Court_Type` | string | | `Arraignment`, `Hearing`, `Trial` |
| 30 | `Case_Number` | string | | Court case number |
| 31 | `Arresting_Agency` | string | | Law enforcement agency |
| 32 | `Mugshot_URL` | string | | Direct URL to mugshot image |
| 33 | `Status` | string | | `In Custody`, `Released`, `Transferred` |
| 34 | `Lead_Score` | int | Auto | 0–100 (set by LeadScorer) |
| 35 | `Lead_Status` | string | Auto | `Hot`, `Warm`, `Cold`, `Disqualified` |
| 36 | `Ingestion_Timestamp` | string | Auto | ISO 8601 UTC |
| 37 | `Source_URL` | string | Auto | URL of the jail roster page |
| 38 | `Scraper_Version` | string | Auto | e.g., `lee_v8.4_python` |
| 39 | `Raw_Data` | string | | JSON blob of unparsed source data |

### Deduplication Key

```
Booking_Number + County = Unique Record
```

MongoDB index: `{ Booking_Number: 1, County: 1 }` (unique)

---

## Lead Score Output

```json
{
  "score": 78,
  "status": "Warm",
  "factors": {
    "bond_amount": 40,
    "recency": 20,
    "charge_severity": 15,
    "residence": 20,
    "adjustments": -17
  },
  "disqualified": false,
  "disqualify_reason": null
}
```

---

## Contact Discovery Output (Phase 4)

```json
{
  "defendant_booking": "2026-123456",
  "defendant_name": "John Smith",
  "county": "Lee",
  "contacts": [
    {
      "name": "Jane Smith",
      "relationship": "household_member",
      "phone": "+12395551234",
      "email": null,
      "source": "voter_registration",
      "confidence": 0.85,
      "discovered_at": "2026-04-22T16:00:00Z"
    }
  ]
}
```

---

## Scraper Stats Output

```json
{
  "county": "Lee",
  "records_scraped": 47,
  "hot_leads": 3,
  "warm_leads": 12,
  "disqualified": 28,
  "new_records": 5,
  "duplicates_skipped": 42,
  "elapsed_seconds": 8.3,
  "writer_results": [
    { "writer": "MongoWriter", "inserted": 5, "updated": 2 },
    { "writer": "SheetsWriter", "appended": 5 }
  ]
}
```

---

## MongoDB Collections

### `arrests`
Primary collection. One document per arrest record.

**Indexes:**
- `{ Booking_Number: 1, County: 1 }` — unique, dedup key
- `{ County: 1, Booking_Date: -1 }` — county queries
- `{ Lead_Status: 1, Lead_Score: -1 }` — hot lead queries
- `{ Ingestion_Timestamp: -1 }` — recency queries
- `{ Status: 1 }` — in-custody filters

### `contacts` (Phase 4)
Discovered contact information for defendants.

### `scraper_runs`
Execution logs for every scraper run.

### `tenants` (Phase 7)
Multi-tenant configuration for licensees.
