> Master reference for every Georgia county jail roster. Updated as scrapers are built and validated.
> **Last Updated:** 2026-07-10 | **Active Scrapers:** 38 (EAS batch + Tier 1s) | **Total Counties:** 159

---

## Legend
| Status | Meaning |
|--------|---------|
| ✅ Active | Scraper built and validated |
| 🔄 Building | Scraper file exists, not yet validated |
| 🔵 Validated | URL confirmed, scraper not yet built |
| ✅ Active | URL unconfirmed, needs manual investigation |
| 🔴 Blocked | Anti-bot, reCAPTCHA, or no public roster |

---

## Tier 1 — EAS Batch Runner (27 Counties)
*All these counties use offenderindex.com and are scraped simultaneously by `eas_batch_runner.py`.*

| County | EAS Slug | Status | Interval |
|--------|----------|--------|----------|
| **Atkinson** | `atkinsoncoga` | ✅ Active | 60 min |
| **Ben Hill** | `benhillcoga` | ✅ Active | 60 min |
| **Berrien** | `berriencoga` | ✅ Active | 60 min |
| **Butts** | `buttscoga` | ✅ Active | 60 min |
| **Chattooga** | `chattoogacoga` | ✅ Active | 60 min |
| **Cook** | `cookcoga` | ✅ Active | 60 min |
| **Decatur** | `decaturcoga` | ✅ Active | 60 min |
| **Elbert** | `elbertcoga` | ✅ Active | 60 min |
| **Fannin** | `fannincoga` | ✅ Active | 60 min |
| **Gilmer** | `gilmercoga` | ✅ Active | 60 min |
| **Gordon** | `gordoncoga` | ✅ Active | 60 min |
| **Jackson** | `jacksoncoga` | ✅ Active | 60 min |
| **Jeff Davis** | `jeffdaviscoga` | ✅ Active | 60 min |
| **Jenkins** | `jenkinscoga` | ✅ Active | 60 min |
| **Laurens** | `laurenscoga` | ✅ Active | 60 min |
| **Lee** | `leecoga` | ✅ Active | 60 min |
| **Lincoln** | `lincolncoga` | ✅ Active | 60 min |
| **Madison** | `madisoncoga` | ✅ Active | 60 min |
| **Newton** | `newtoncoga` | ✅ Active | 60 min |
| **Pierce** | `piercecoga` | ✅ Active | 60 min |
| **Tift** | `tiftcoga` | ✅ Active | 60 min |
| **Towns** | `townscoga` | ✅ Active | 60 min |
| **Ware** | `warecoga` | ✅ Active | 60 min |
| **Wayne** | `waynecoga` | ✅ Active | 60 min |
| **Webster** | `webstercoga` | ✅ Active | 60 min |
| **Wheeler** | `wheelercoga` | ✅ Active | 60 min |
| **Worth** | `worthcoga` | ✅ Active | 60 min |

---

## Tier 2 — Metro Atlanta & Major Portals (11 Counties)

| # | County | JMS / Method | Scraper File | Status | Interval |
|---|--------|-------------|--------------|--------|----------|
| 1 | **Fulton** | Socrata API | `fulton.py` | ✅ Active | Daily |
| 2 | **Chatham** | Custom HTML | `chatham.py` | ✅ Active | 30 min |
| 3 | **Walton** | XML Feed | `walton.py` | ✅ Active | 30 min |
| 4 | **Forsyth** | P2C | `forsyth.py` | ✅ Active | 60 min |
| 5 | **Hall** | P2C | `hall.py` | ✅ Active | 60 min |
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
| **Gwinnett** | `gwinnettcountysheriff.com/SmartWebClient/Jail.aspx` | 🔵 Validated | SmartWebClient |
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
Fulton County (Atlanta) is the largest prize. Instead of scraping their Tyler Odyssey portal, we use their official Socrata Open Data API (`sharefulton.fultoncountyga.gov`). This guarantees 100% accurate daily data without rate-limiting or CAPTCHA issues.

### 3. Court Outcome Tracking Architecture
Because Georgia has strong centralized court systems (PeachCourt/eCourts), the `ArrestRecord` schema is being extended to track outcomes:
- **`Court_Disposition`**: Track dismissals vs convictions
- **`Bond_Forfeiture_Flag`**: Critical for bondsmen to know when a client FTAs
- **`FTA_Date`**: The exact date of failure to appear

*Next Steps: Build the remaining custom HTML scrapers for Cobb, Gwinnett, and Richmond.*
