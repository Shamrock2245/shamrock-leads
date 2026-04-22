# 🗺️ Florida County Registry — All 67 Counties

> Master reference for every Florida county jail roster. Updated as scrapers are built and validated.

---

## Legend

| Status | Meaning |
|--------|---------|
| ✅ Active | Scraper running in production |
| 🔄 Porting | Being migrated to Python |
| 🔵 Validated | URL confirmed, scraper not yet built |
| 🟡 Needs Recon | URL unconfirmed, needs manual investigation |
| 🔴 Blocked | Anti-bot, CAPTCHA, or no public roster |

---

## Tier 1 — SWFL Core (Priority)

| # | County | JMS Vendor | Roster URL | Scraper | Status | Interval |
|---|--------|-----------|------------|---------|--------|----------|
| 1 | **Lee** | Odyssey (Tyler) | `sheriff.lcso.org` API | `lee.py` | ✅ Active | 20 min |
| 2 | **Charlotte** | Custom | `ccso.org` | `charlotte.py` | 🔄 Porting | 30 min |
| 3 | **Collier** | Odyssey | `collier.gov` | `collier.py` | 🔄 Porting | 30 min |
| 4 | **DeSoto** | JailTracker | `desotoso.com` | `desoto.py` | 🔄 Porting | 60 min |
| 5 | **Hendry** | JailTracker | `hendrysheriff.org` | `hendry.py` | 🔄 Porting | 60 min |
| 6 | **Manatee** | New World | `manateesheriff.com` | `manatee.py` | 🔄 Porting | 30 min |
| 7 | **Sarasota** | Odyssey | `sarasotasheriff.org` | `sarasota.py` | 🔄 Porting | 30 min |

## Tier 2 — Tampa Bay / Central FL

| # | County | JMS Vendor | Status |
|---|--------|-----------|--------|
| 8 | Hillsborough | New World | 🔵 Validated |
| 9 | Pinellas | JailTracker | 🔵 Validated |
| 10 | Pasco | Custom | 🔵 Validated |
| 11 | Polk | Odyssey | 🔵 Validated |
| 12 | Hernando | Custom | 🔵 Validated |
| 13 | Citrus | JailTracker | 🔵 Validated |
| 14 | Sumter | Custom | 🔵 Validated |
| 15 | Lake | Superion | 🔵 Validated |

## Tier 3 — South FL / Metro

| # | County | JMS Vendor | Status |
|---|--------|-----------|--------|
| 16 | Palm Beach | Custom | 🔵 Validated |
| 17 | Broward | Custom | 🔵 Validated |
| 18 | Miami-Dade | Custom | 🟡 Needs Recon |
| 19 | Monroe | Custom | 🟡 Needs Recon |
| 20 | Martin | JailTracker | 🔵 Validated |
| 21 | St. Lucie | JailTracker | 🔵 Validated |
| 22 | Indian River | JailTracker | 🔵 Validated |
| 23 | Okeechobee | Custom | 🟡 Needs Recon |
| 24 | Glades | JailTracker | 🔵 Validated |
| 25 | Highlands | JailTracker | 🔵 Validated |

## Tier 4 — North Central FL

| # | County | JMS Vendor | Status |
|---|--------|-----------|--------|
| 26 | Alachua | Custom | 🔵 Validated |
| 27 | Marion | JailTracker | 🔵 Validated |
| 28 | Volusia | Custom | 🔵 Validated |
| 29 | Brevard | Odyssey | 🔵 Validated |
| 30 | Seminole | Custom | 🔵 Validated |
| 31 | Orange | Custom | 🔵 Validated |
| 32 | Osceola | Custom | 🟡 Needs Recon |
| 33 | Flagler | Custom | 🟡 Needs Recon |
| 34 | Putnam | JailTracker | 🔵 Validated |
| 35 | Levy | Custom | 🟡 Needs Recon |

## Tier 5 — Panhandle / NW FL

| # | County | JMS Vendor | Status |
|---|--------|-----------|--------|
| 36 | Escambia | Odyssey | 🔵 Validated |
| 37 | Santa Rosa | Custom | 🟡 Needs Recon |
| 38 | Okaloosa | Custom | 🔵 Validated |
| 39 | Walton | Custom | 🟡 Needs Recon |
| 40 | Bay | Custom | 🔵 Validated |
| 41 | Jackson | Custom | 🟡 Needs Recon |
| 42 | Leon | Odyssey | 🔵 Validated |
| 43 | Gadsden | Custom | 🟡 Needs Recon |
| 44 | Wakulla | Custom | 🟡 Needs Recon |

## Tier 6 — NE FL / First Coast

| # | County | JMS Vendor | Status |
|---|--------|-----------|--------|
| 45 | Duval (Jacksonville) | Custom | 🔵 Validated |
| 46 | St. Johns | Custom | 🔵 Validated |
| 47 | Clay | Custom | 🟡 Needs Recon |
| 48 | Nassau | Custom | 🟡 Needs Recon |
| 49 | Baker | Custom | 🟡 Needs Recon |
| 50 | Bradford | Custom | 🟡 Needs Recon |
| 51 | Columbia | Custom | 🟡 Needs Recon |
| 52 | Suwannee | Custom | 🟡 Needs Recon |

## Tier 7 — Rural / Small Counties

| # | County | JMS Vendor | Status |
|---|--------|-----------|--------|
| 53 | Hardee | Custom | 🟡 Needs Recon |
| 54 | Hamilton | Custom | 🟡 Needs Recon |
| 55 | Lafayette | Custom | 🟡 Needs Recon |
| 56 | Madison | Custom | 🟡 Needs Recon |
| 57 | Taylor | Custom | 🔵 Validated |
| 58 | Dixie | Custom | 🔵 Validated |
| 59 | Gilchrist | Custom | 🟡 Needs Recon |
| 60 | Union | Custom | 🟡 Needs Recon |
| 61 | Calhoun | Custom | 🟡 Needs Recon |
| 62 | Gulf | Custom | 🟡 Needs Recon |
| 63 | Holmes | Custom | 🟡 Needs Recon |
| 64 | Jefferson | Custom | 🟡 Needs Recon |
| 65 | Liberty | Custom | 🟡 Needs Recon |
| 66 | Washington | Custom | 🟡 Needs Recon |
| 67 | Franklin | Custom | 🟡 Needs Recon |

---

## JMS Vendor Scraping Patterns

### Odyssey (Tyler Technologies)
- **Pattern**: REST API with JSON responses
- **Auth**: None (public inmate search)
- **Pagination**: Offset-based (`?page=1&size=50`)
- **Charges**: Separate API endpoint per booking
- **Counties**: Lee, Collier, Sarasota, Polk, Brevard, Escambia, Leon

### JailTracker (Black Creek ISC)
- **Pattern**: Paginated HTML tables
- **Auth**: None
- **Anti-bot**: Occasional CAPTCHA, rate limiting
- **Pagination**: Page links in HTML
- **Counties**: DeSoto, Hendry, Pinellas, Citrus, Martin, St. Lucie, Indian River, Marion, Putnam, Highlands

### New World (Tyler Technologies)
- **Pattern**: HTML table scraping
- **Auth**: None
- **Pagination**: Single-page or next/prev links
- **Counties**: Manatee, Hillsborough

### Superion (CentralSquare)
- **Pattern**: XML/SOAP or HTML
- **Auth**: None
- **Counties**: Lake

### Custom / In-House
- **Pattern**: Varies wildly — GET requests with regex extraction
- **Each county is unique**: Requires individual reverse-engineering
- **Most common**: Simple HTML tables with no pagination

---

## Adding a New County

```bash
# 1. Recon: Find the roster URL
# 2. Identify JMS vendor (check HTML source)
# 3. Copy the closest template scraper
# 4. Adapt parsing logic
# 5. Test with: python main.py --county <name> --once
# 6. Add to scheduler config
```

See `.agent/workflows/add-county-scraper.md` for the detailed procedure.
