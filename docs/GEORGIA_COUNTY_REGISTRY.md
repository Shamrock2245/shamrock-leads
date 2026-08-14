> Master reference for every Georgia county jail roster. Updated as scrapers are built and validated.
> **Last Updated:** 2026-08-11 | **Registered (dashboard):** 85 GA labels in `REGISTERED_COUNTIES` | **Total Counties:** 159  
> Authoritative multi-state scale: root [`STATUS.md`](../STATUS.md)

---

## Legend
| Status | Meaning |
|--------|---------|
| ✅ Active | Scraper built and validated |
| ⏸ Revalidation | Historical source entry; unscheduled until the live endpoint and source booking identifier are revalidated |
| 🔄 Building | Scraper file exists, not yet validated |
| 🔵 Validated | URL confirmed, scraper not yet built |
| ✅ Active | URL unconfirmed, needs manual investigation |
| 🔴 Blocked | Anti-bot, reCAPTCHA, or no public roster |

---

## Tier 1 — EAS Revalidation Hold (30 historical candidate counties)

> The historical EAS list is retained for source reconnaissance only. `eas_batch_runner.py` is **not scheduler-registered**: its endpoint and source-provided booking identifiers must be revalidated county by county before any production ingestion. See [`recon/GEORGIA_SOURCE_VALIDATION_2026-08-12.md`](./recon/GEORGIA_SOURCE_VALIDATION_2026-08-12.md).

| County | EAS Slug | Status | Interval |
|--------|----------|--------|----------|
| **Atkinson** | `atkinsoncoga` | ⏸ Revalidation | — |
| **Ben Hill** | `benhillcoga` | ⏸ Revalidation | — |
| **Berrien** | `berriencoga` | ⏸ Revalidation | — |
| **Butts** | `buttscoga` | ⏸ Revalidation | — |
| **Chattooga** | `chattoogacoga` | ⏸ Revalidation | — |
| **Cook** | `cookcoga` | ⏸ Revalidation | — |
| **Decatur** | `decaturcoga` | ⏸ Revalidation | — |
| **Elbert** | `elbertcoga` | ⏸ Revalidation | — |
| **Fannin** | `fannincoga` | ⏸ Revalidation | — |
| **Gilmer** | `gilmercoga` | ⏸ Revalidation | — |
| **Gordon** | `gordoncoga` | ⏸ Revalidation | — |
| **Jackson** | `jacksoncoga` | ⏸ Revalidation | — |
| **Jeff Davis** | `jeffdaviscoga` | ⏸ Revalidation | — |
| **Jenkins** | `jenkinscoga` | ⏸ Revalidation | — |
| **Laurens** | `laurenscoga` | ⏸ Revalidation | — |
| **Lee** | `leecoga` | ⏸ Revalidation | — |
| **Lincoln** | `lincolncoga` | ⏸ Revalidation | — |
| **Madison** | `madisoncoga` | ⏸ Revalidation | — |
| **Newton** | `newtoncoga` | ⏸ Revalidation | — |
| **Pierce** | `piercecoga` | ⏸ Revalidation | — |
| **Tift** | `tiftcoga` | ⏸ Revalidation | — |
| **Towns** | `townscoga` | ⏸ Revalidation | — |
| **Ware** | `warecoga` | ⏸ Revalidation | — |
| **Wayne** | `waynecoga` | ⏸ Revalidation | — |
| **Webster** | `webstercoga` | ⏸ Revalidation | — |
| **Wheeler** | `wheelercoga` | ⏸ Revalidation | — |
| **McDuffie** | `mcduffiecoga` | ⏸ Revalidation | — |
| **Meriwether** | `meriwethercoga` | ⏸ Revalidation | — |
| **Warren** | `warrencoga` | ⏸ Revalidation | — |
| **Worth** | `worthcoga` | ⏸ Revalidation | — |

---

> **Legacy P2C safety:** Columbia, Coweta, Dougherty, Forsyth, Hall, and Spalding are registered scheduler paths but now fail closed. Their current official public sources are restricted or lack a booking-safe bulk identity boundary. See [`LEGACY_P2C_SOURCE_SAFETY.md`](LEGACY_P2C_SOURCE_SAFETY.md); do not re-enable broad searches or bypass access controls.

## Tier 2 — Metro Atlanta & Major Portals (11 Counties)

| # | County | JMS / Method | Scraper File | Status | Interval |
|---|--------|-------------|--------------|--------|----------|
| 1 | **Fulton** | Socrata API | `fulton.py` | ✅ Active | Daily |
| 2 | **Chatham** | Custom HTML | `chatham.py` | ✅ Active | 30 min |
| 3 | **Walton** | XML Feed | `walton.py` | ✅ Active | 30 min |
| 4 | **Forsyth** | P2C | `forsyth.py` | ⚠ Fail closed | 60 min |
| 5 | **Hall** | P2C | `hall.py` | ⚠ Fail closed | 60 min |
| 6 | **Douglas** | Zuercher | `douglas.py` | ✅ Active | 90 min |
| 7 | **Houston** | Zuercher | `houston.py` | 🔄 Building | 90 min |
| 8 | **Floyd** | Zuercher | `floyd.py` | 🔄 Building | 90 min |
| 9 | **Catoosa** | Zuercher | `catoosa.py` | 🔄 Building | 90 min |
| 10 | **Lowndes** | Tyler Odyssey | `lowndes.py` | ✅ Active | 60 min |
| 11 | **Banks** | Southern SW | `banks.py` | ✅ Active | 90 min |

---

## Tier 3 — Southern Software Fleet (3 Remaining)

| County | AgencyID | Status | Interval |
|--------|----------|--------|----------|
| **Decatur** | `DecaturCoSOGA` | 🔵 Validated | 90 min |
| **Lee** | `LeeCoSOGA` | 🔵 Validated | 90 min |
| **Oglethorpe** | `OglethorpeCoGA` | 🔵 Validated | 90 min |

---

## Tier 4 — Custom HTML Portals (Validated, Need Scrapers)

| County | Portal URL | Status | Notes |
|--------|------------|--------|-------|
| **Cobb** | `cobbsheriff.org/inmates/adult-detention-center` | 🔵 Validated | High priority metro |
| **Gwinnett** | `gwinnettcountysheriff.com/smartwebclient/` | ⏳ Fail closed — deployed 2026-08-14 | Public last-24-hours view has booking key/time but abbreviates given names; existing job emits no records until a supported complete-identity bulk contract is available. Public production hosts are healthy; no Gwinnett writes or alerts are expected from the safety guard. |
| **Richmond** | `richmondcountysheriffsoffice.com/inmate-inquiry.cfm` | 🔵 Validated | Augusta area |
| **Bartow** | `bartowcountyga.gov/sheriff/inmate-search` | 🔵 Validated | Clean HTML table |
| **Glynn** | `glynncountysheriff.org/inmate-search` | 🔵 Validated | Coastal area |
| **Newton** | `newtoncountysheriff.com/inmate-search` | 🔵 Validated | Also has EAS portal |

---

## Data Organization & Intelligence Strategy

Georgia data requires a slightly different approach than Florida due to O.C.G.A. restrictions on mugshots and the prevalence of statewide data systems.

### 1. The EAS Advantage
Over 25% of Georgia counties use a single system (`offenderindex.com`). This is our core operational base. The `eas_batch_runner.py` is scheduled to hit all 27 counties sequentially every hour, providing a massive, low-effort lead pipeline for rural and mid-size counties.

### 2. Socrata Integration (Fulton)
Fulton County's official Socrata API remains the configured source path, but its metadata and data endpoint returned public HTTP 403 during aggregate-only validation on 2026-08-14. The shared Socrata parser now fails closed: it emits records only with a complete public identity, source-issued booking identifier, and booking/arrest date, and never synthesizes a booking key. The `569441c` rollout completed successfully and public hosts were verified healthy; Fulton remains registered but is recon-only until the county restores a supported public source contract. No access-control bypass is permitted, and no Fulton writes or alerts are expected while the source stays inaccessible.

### 3. Court Outcome Tracking Architecture
Because Georgia has strong centralized court systems (PeachCourt/eCourts), the `ArrestRecord` schema is being extended to track outcomes:
- **`Court_Disposition`**: Track dismissals vs convictions
- **`Bond_Forfeiture_Flag`**: Critical for bondsmen to know when a client FTAs
- **`FTA_Date`**: The exact date of failure to appear

*Next Steps: Re-validate Cobb and Richmond public contracts before repair work. Gwinnett is explicitly fail-closed pending a supported bulk view with complete names plus source booking fields.*
