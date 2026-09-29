# Tennessee County Scrapers — Promotion & Source Contract Verification

**Date:** 2026-09-29  
**Platform Footprint:** Palmetto Surety Footprint (Tennessee)  
**Authoritative Source Contract State:** `dashboard/extensions.py` (`SCRAPER_SOURCE_STATES`)  
**Canonical Recon Matrix:** `docs/recon/COUNTY_SOURCE_CONTRACT_MATRIX.md` (947 county worklist)  

---

## 1. Executive Summary

Four high-volume Tennessee county scrapers have been re-engineered to interface directly with official public county portals without synthetic/hash keys, CAPTCHA bypasses, or TLS circumvention. All four have been validated with full data contract compliance, promoted from `fail_closed` to `verified_public`, and live-smoked directly into MongoDB Atlas (`ShamrockBailDB.arrests`).

Together with previously verified **Putnam County**, the platform now actively operates **5 productive Tennessee counties** representing **1,678 stored arrest records**, including **407 Hot leads** and **499 Warm leads**.

| State | County | FIPS | Roster Portal / Feed | Official Key | Status | Live DB Records | Lead Breakdown |
|-------|--------|------|----------------------|--------------|--------|-----------------|----------------|
| TN | **Davidson** | 037 | DCSO RecentBookings + Details | DCSO JMS Number (7 digits) | `verified_public` | 121 | 🔥 13 Hot · 🟡 36 Warm |
| TN | **Knox** | 093 | Knox Sheriff 24h Arrests + Inmate Pop | Knox IDN# (7 digits) | `verified_public` | 51 | 🔥 13 Hot · 🟡 25 Warm |
| TN | **Sumner** | 165 | MyOCV `inmatesV3` Real-Time Feed | Inmate ID (6 digits) | `verified_public` | 816 | 🔥 282 Hot · 🟡 338 Warm |
| TN | **Shelby** | 157 | Memphis 201 Poplar IML Portal | Booking Number (8 digits) | `verified_public` | 150 | 🔥 13 Hot · 🟡 50 Warm |
| TN | **Putnam** | 141 | Putnam Sheriff ISOMS Roster | Stable Public Name/Intake Surrogate | `verified_public` | 540 | 🔥 87 Hot · 🟡 103 Warm |
| **Total** | **5 Counties** | — | — | — | — | **1,678** | **🔥 407 Hot · 🟡 499 Warm** |

---

## 2. County Source Contracts & Verification Details

### A. Davidson County (Nashville — TN 037)
- **Portal URL:** `https://dcso.nashville.gov/Search/RecentBookings` + `/Search/Details/{jms_id}`
- **Network Layer:** Plain HTTPS requests via `requests.Session(verify=True)`. Zero CAPTCHA or WAF challenges.
- **Source Identifier:** Official DCSO `JMS Number` (e.g., `1077001`). Extracted from detail link or button `onclick`. Control Number retained in `Person_ID`.
- **Contract Fields:**
  - `Full_Name`: DCSO parsed name (`Last, First Middle`)
  - `DOB`: Extracted from detail inmate info (`MM/DD/YYYY`)
  - `Facility`: DCSO Downtown Detention Center, etc.
  - `Booking_Date`: Admitted timestamp
  - `Charges`: Parsed from `#charge-information` with court warrant numbers (e.g., `AGGRAVATED ASSAULT (Warrant: GS123456)`)
  - `Bond_Amount`: Summed from all active charge bonds (e.g., `$17,500.00`)
- **Write Smoke Results:**
  - Scraped: 121 records in 8.3s
  - Mongo Write: 121 new records inserted into `ShamrockBailDB.arrests`
  - Scoring: 🔥 13 Hot | 🟡 36 Warm | ❌ 69 Disqualified

### B. Knox County (Knoxville — TN 093)
- **Portal URL:** `https://sheriff.knoxcountytn.gov/index.php` (24-hour arrests) + `inmate.php` (active population)
- **Network Layer:** Plain HTTPS requests via `requests.Session(verify=True)`. Zero CAPTCHA or Cloudflare.
- **Source Identifier:** Official Knox County `IDN#` (e.g., `1720300`).
- **Contract Fields:**
  - `Full_Name`: Inmate header (`Last, First Middle`)
  - `DOB`: Extracted from header (`D.O.B. MM/DD/YYYY`)
  - `Booking_Date`: Booked timestamp from charges table
  - `Charges`: Multiple charge descriptions delimited by semicolons
  - `Bond_Amount`: Summed from charge bond entries
  - `Court_Date`: Next hearing date
  - `Facility`: Knox County Detention Facility
- **Write Smoke Results:**
  - Scraped: 51 records in 2.1s
  - Mongo Write: 51 new records inserted into `ShamrockBailDB.arrests`
  - Scoring: 🔥 13 Hot | 🟡 25 Warm | ❌ 12 Disqualified

### C. Sumner County (Gallatin — TN 165)
- **Portal URL:** `https://apps.myocv.com/feed/rtjb/a46036101/inmatesV3`
- **Network Layer:** Direct JSON endpoint via `requests.get(verify=True)`. Zero CAPTCHA or blocking.
- **Source Identifier:** Numeric `Inmate ID` (e.g., `263108`).
- **Contract Fields:**
  - `Full_Name`: `title` field (`Last, First Middle`)
  - `Booking_Date`: Cleaned timestamp from content HTML
  - `Age_At_Arrest`, `Race`, `Sex`: Extracted from metadata
  - `Charges`: Cleaned charge descriptions with trailing `(Bond: $X)` strings cleanly stripped
  - `Bond_Amount`: Exact parsed bond sum
- **Write Smoke Results:**
  - Scraped: 668 records in 1.6s
  - Mongo Write: 595 new records, 73 updated records in `ShamrockBailDB.arrests`
  - Scoring: 🔥 134 Hot | 🟡 338 Warm | ❌ 174 Disqualified

### D. Shelby County (Memphis — TN 157)
- **Portal URL:** `https://imljail.shelbycountytn.gov/IML` (201 Poplar Jail)
- **Network Layer:** Plain HTTPS session with form-data postback (`verify=True`). No CAPTCHA or WAF.
- **Source Identifier:** Official 8-digit `Booking Number` (e.g., `26115215`). Permanent ID retained in `Person_ID`.
- **Contract Fields:**
  - `Full_Name`: Cleaned listing name (`Last, First Middle`)
  - `DOB`: DOB from listing table
  - `Commitment_Date`: Commitment date from detail page
  - `Court_Date`: Next Court Date from detail page
  - `Charges`: Structured charges with statute codes and felony/misdemeanor grades
  - `Bond_Amount`: Grand Total bond amount (e.g., `$25,000.00`)
- **Write Smoke Results:**
  - Scraped: 150 records across 5 pages with detail enrichment in 90.5s
  - Mongo Write: 150 new records inserted into `ShamrockBailDB.arrests`
  - Scoring: 🔥 13 Hot | 🟡 50 Warm | ❌ 85 Disqualified

---

## 3. Strict Guard Enforcement for Non-Emitting Counties

All other registered Tennessee county scrapers remain strictly guarded as `fail_closed` in `dashboard/extensions.py` and `scrapers/counties_tn/`:

1. **Montgomery County (TN 125):** The legacy `mcsojail.countygovservices.com` domain pointed to Montgomery, Alabama. The official Montgomery County TN portal (`api.mcgtn.org`) only provides a two-field individual name search and does not expose an open broad listing. Remains `fail_closed`.
2. **Rutherford County (TN 149) & Williamson County (TN 187):** No public web inmate roster is published (directs to VINE, phone, or inmate commissary/visitation services). Remain `fail_closed`.
3. **Blount (TN 009), Maury (TN 119), Wilson (TN 189):** JailTracker implementations requiring interactive image CAPTCHA. Remain `fail_closed`.
4. **Southern Software Counties:** Bedford (003), Bradley (011), Coffee (031), Giles (055), Hamblen (063), Lincoln (103), Robertson (147), Sevier (155), Washington (179) redirect to the generic Citizen Connect directory (`index2.php`) and do not provide public inmate inquiry rosters. Remain `fail_closed`.

---

## 4. Test Suite & Matrix Integrity

- **Unit Tests:** `tests/test_tennessee_scrapers.py` verifies synthetic HTML and JSON parsing contracts for Davidson, Knox, Sumner, and Shelby without live network access (4 passed).
- **Matrix Drift:** `tests/test_source_state_drift.py` verifies 0 drift across `SCRAPER_SOURCE_STATES`, `county_source_contract_evidence.json`, `live_emitter_evidence.json`, and `COUNTY_SOURCE_CONTRACT_MATRIX.md` (6 passed).
- **CI Bridge:** `tests/test_source_contract_run_guard.py` wires the Tennessee test suite into the CI runner.
