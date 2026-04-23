# 🗺️ Florida County Registry — All 67 Counties

> Master reference for every Florida county jail roster. Updated as scrapers are built and validated.
> **Last Updated:** 2026-04-23 | **Active Scrapers:** 20 | **Scheduled Counties:** 20

---

## Legend

| Status | Meaning |
|--------|---------|
| ✅ Active | Scraper running in production (Hetzner VPS) |
| 🔄 Building | Scraper file exists, not yet validated |
| 🔵 Validated | URL confirmed, scraper not yet built |
| 🟡 Needs Recon | URL unconfirmed, needs manual investigation |
| 🔴 Blocked | Anti-bot, CAPTCHA, or no public roster |

---

## Tier 1 — SWFL Core (7 Counties)

| # | County | JMS Vendor | Scraper File | Status | Interval | Last Verified |
|---|--------|-----------|--------------|--------|----------|---------------|
| 1 | **Lee** | Odyssey (Tyler) | `lee.py` | ✅ Active | 20 min | 2026-04-23 |
| 2 | **Collier** | Odyssey | `collier.py` | ✅ Active | 30 min | 2026-04-23 |
| 3 | **Charlotte** | Custom | `charlotte.py` | ✅ Active | 45 min | 2026-04-23 |
| 4 | **Hendry** | JailTracker | `hendry.py` | ✅ Active | 120 min | 2026-04-23 |
| 5 | **DeSoto** | JailTracker | `desoto.py` | ✅ Active | 60 min | 2026-04-23 |
| 6 | **Manatee** | New World | `manatee.py` | ✅ Active | 45 min | 2026-04-23 |
| 7 | **Sarasota** | Odyssey | `sarasota.py` | ✅ Active | 60 min | 2026-04-23 |

## Tier 4 — Central & East FL Expansion (6 Counties)

| # | County | JMS Vendor | Scraper File | Status | Interval | Last Verified |
|---|--------|-----------|--------------|--------|----------|---------------|
| 8 | **Orange** | Custom | `orange.py` | ✅ Active | 90 min | 2026-04-23 |
| 9 | **Pinellas** | JailTracker | `pinellas.py` | ✅ Active | 90 min | 2026-04-23 |
| 10 | **Polk** | Odyssey | `polk.py` | ✅ Active | 120 min | 2026-04-23 |
| 11 | **Osceola** | Custom | `osceola.py` | ✅ Active | 120 min | 2026-04-23 |
| 12 | **Seminole** | Custom | `seminole.py` | ✅ Active | 90 min | 2026-04-23 |
| 13 | **Palm Beach** | Custom | `palm_beach.py` | ✅ Active | 120 min | 2026-04-23 |

## Tier 5 — Statewide High-Pop (7 Counties)

| # | County | JMS Vendor | Scraper File | Status | Interval | Last Verified |
|---|--------|-----------|--------------|--------|----------|---------------|
| 14 | **Hillsborough** | New World | `hillsborough.py` | ✅ Active | 90 min | 2026-04-23 |
| 15 | **Broward** | Custom | `broward.py` | ✅ Active | 60 min | 2026-04-23 |
| 16 | **Duval** | Custom | `duval.py` | ✅ Active | 90 min | 2026-04-23 |
| 17 | **Volusia** | Custom | `volusia.py` | ✅ Active | 90 min | 2026-04-23 |
| 18 | **Brevard** | Odyssey | `brevard.py` | ✅ Active | 120 min | 2026-04-23 |
| 19 | **Pasco** | Custom | `pasco.py` | ✅ Active | 90 min | 2026-04-23 |
| 20 | **Escambia** | Odyssey | `escambia.py` | ✅ Active | 120 min | 2026-04-23 |

---

## Remaining Counties — Not Yet Built

### Next Priority (Validated URLs)

| # | County | JMS Vendor | Status | Notes |
|---|--------|-----------|--------|-------|
| 21 | Hernando | Custom | 🔵 Validated | Tampa Bay area |
| 22 | Citrus | JailTracker | 🔵 Validated | Nature Coast |
| 23 | Sumter | Custom | 🔵 Validated | The Villages |
| 24 | Lake | Superion | 🔵 Validated | Central FL |
| 25 | Martin | JailTracker | 🔵 Validated | Treasure Coast |
| 26 | St. Lucie | JailTracker | 🔵 Validated | Treasure Coast |
| 27 | Indian River | JailTracker | 🔵 Validated | Treasure Coast |
| 28 | Highlands | JailTracker | 🔵 Validated | Heartland |
| 29 | Alachua | Custom | 🔵 Validated | Gainesville |
| 30 | Marion | JailTracker | 🔵 Validated | Ocala |
| 31 | Leon | Odyssey | 🔵 Validated | Tallahassee |
| 32 | St. Johns | Custom | 🔵 Validated | St. Augustine |
| 33 | Okaloosa | Custom | 🔵 Validated | Panhandle |
| 34 | Bay | Custom | 🔵 Validated | Panama City |
| 35 | Putnam | JailTracker | 🔵 Validated | Palatka |
| 36 | Flagler | Custom | 🟡 Needs Recon | Palm Coast |
| 37 | Glades | JailTracker | 🔵 Validated | Rural Heartland |

### Needs Recon (26 Counties)

| # | County | Status |
|---|--------|--------|
| 38 | Miami-Dade | 🟡 Needs Recon |
| 39 | Monroe | 🟡 Needs Recon |
| 40 | Okeechobee | 🟡 Needs Recon |
| 41 | Santa Rosa | 🟡 Needs Recon |
| 42 | Walton | 🟡 Needs Recon |
| 43 | Jackson | 🟡 Needs Recon |
| 44 | Gadsden | 🟡 Needs Recon |
| 45 | Wakulla | 🟡 Needs Recon |
| 46 | Clay | 🟡 Needs Recon |
| 47 | Nassau | 🟡 Needs Recon |
| 48 | Baker | 🟡 Needs Recon |
| 49 | Bradford | 🟡 Needs Recon |
| 50 | Columbia | 🟡 Needs Recon |
| 51 | Suwannee | 🟡 Needs Recon |
| 52 | Levy | 🟡 Needs Recon |
| 53 | Taylor | 🔵 Validated |
| 54 | Dixie | 🔵 Validated |
| 55 | Hardee | 🟡 Needs Recon |
| 56 | Hamilton | 🟡 Needs Recon |
| 57 | Lafayette | 🟡 Needs Recon |
| 58 | Madison | 🟡 Needs Recon |
| 59 | Gilchrist | 🟡 Needs Recon |
| 60 | Union | 🟡 Needs Recon |
| 61 | Calhoun | 🟡 Needs Recon |
| 62 | Gulf | 🟡 Needs Recon |
| 63 | Holmes | 🟡 Needs Recon |
| 64 | Jefferson | 🟡 Needs Recon |
| 65 | Liberty | 🟡 Needs Recon |
| 66 | Washington | 🟡 Needs Recon |
| 67 | Franklin | 🟡 Needs Recon |

---

## JMS Vendor Scraping Patterns

### Odyssey (Tyler Technologies)
- **Pattern**: REST API with JSON responses
- **Auth**: None (public inmate search)
- **Pagination**: Offset-based (`?page=1&size=50`)
- **Charges**: Separate API endpoint per booking
- **Active Counties**: Lee, Collier, Sarasota, Polk, Brevard, Escambia
- **Unbuilt**: Leon

### JailTracker (Black Creek ISC)
- **Pattern**: Paginated HTML tables
- **Auth**: None
- **Anti-bot**: Occasional CAPTCHA, rate limiting
- **Pagination**: Page links in HTML
- **Active Counties**: DeSoto, Hendry, Pinellas
- **Unbuilt**: Citrus, Martin, St. Lucie, Indian River, Marion, Putnam, Highlands, Glades

### New World (Tyler Technologies)
- **Pattern**: HTML table scraping
- **Auth**: None
- **Pagination**: Single-page or next/prev links
- **Active Counties**: Manatee, Hillsborough

### Custom / In-House
- **Pattern**: Varies — GET requests, HTML parsing, API reverse-engineering
- **Active Counties**: Charlotte, Orange, Osceola, Seminole, Palm Beach, Broward, Duval, Volusia, Pasco
- **Each county is unique**: Requires individual reverse-engineering

### Superion (CentralSquare)
- **Pattern**: XML/SOAP or HTML
- **Auth**: None
- **Unbuilt**: Lake

---

## Self-Healing URL Patterns

When a jail URL breaks, check these common patterns per vendor:

| Vendor | Common URL Pattern | Fallback Pattern |
|--------|-------------------|------------------|
| Odyssey | `https://[county]sheriff.org/api/inmates` | `https://[county].tylerhost.net/api/inmates` |
| JailTracker | `https://omsweb.public-safety-cloud.com/jtclientweb/jailtracker/index/[ID]` | Google: `site:public-safety-cloud.com [county]` |
| New World | `https://[county]sheriff.org/inmates` | Check for `inmateSearch.aspx` path |
| Custom | `https://[county]sheriff.org/inmate-search` | Google: `[county] florida sheriff inmate search` |

**HTTPS Migration Note**: Many counties migrate from HTTP to HTTPS without redirect. Always try HTTPS first. Some rural counties (Dixie, Taylor) use non-standard ports (:8443).

---

## Adding a New County

See `.agent/workflows/add-county-scraper.md` and `.agent/skills/scraper-builder/SKILL.md` for detailed procedures.

```bash
# Quick start:
# 1. Recon: Find roster URL → Identify JMS vendor
# 2. Copy closest template scraper
# 3. Adapt parsing logic
# 4. Test: python main.py <county_name>
# 5. Register in main.py with interval
# 6. Update this file: mark ✅ Active
```
