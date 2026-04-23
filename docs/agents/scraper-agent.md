# Scraper Agent — "The Clerk"

> **Status:** `[IMPLEMENTED — Phase 1]`
> **This is the only agent that exists in production code today.**

---

## Role

The Clerk is the arrest data ingestion pipeline. It scrapes county jail rosters, normalizes data into `ArrestRecord` objects, scores each record, writes to MongoDB, and fires Slack alerts.

---

## How It Works

### Pipeline

```
Schedule fires → BaseScraper.run()
  ↓
Pre-flight URL check (HEAD request)
  ↓
County-specific scrape_arrests() → raw HTML/JSON
  ↓
Parse into list[ArrestRecord]
  ↓
DedupEngine checks County + Booking_Number
  ↓
LeadScorer scores each record (0-100)
  ↓
MongoWriter upserts to `arrests` collection
  ↓
SlackNotifier alerts on hot leads
  ↓
Dashboard updates
```

### Key Files

| File | Purpose |
|------|---------|
| `scrapers/base_scraper.py` | Abstract base class with self-healing |
| `scrapers/counties/*.py` | 20 county-specific implementations |
| `scoring/lead_scorer.py` | 0-100 scoring rules |
| `writers/mongo_writer.py` | MongoDB upsert by County + Booking_Number |
| `writers/sheets_writer.py` | Legacy Google Sheets writer |
| `writers/slack_notifier.py` | Slack webhook alerts |
| `core/models.py` | ArrestRecord dataclass (39 fields) |
| `core/dedup.py` | In-memory LRU + MongoDB dedup |
| `core/scheduler.py` | APScheduler with per-county intervals |

---

## Self-Healing Features

| Feature | Behavior |
|---------|----------|
| Pre-flight check | HEAD request before scraping — catches 404/403/SSL early |
| Retry with backoff | 3 attempts: 2s, 4s, 8s delays |
| Error classification | `network`, `anti_bot`, `url_changed`, `parse_error`, `ssl_error`, `rate_limited` |
| Auto-disable | After 5 consecutive failures, scraper disabled |
| Auto-re-enable | Disabled scraper attempts one recovery per interval |
| Failure history | Last 10 failures stored with timestamps |
| Force re-enable | `scraper.force_enable()` for human override |

---

## Per-County Operational Details

### Tier 1 — SWFL Core (7 Counties)

#### Lee County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/lee.py` |
| Class | `LeeCountyScraper` |
| JMS Vendor | Odyssey (Tyler Technologies) |
| Base URL | `https://www.sheriffleefl.org` |
| Method | REST API — JSON responses (bookings API + charges API) |
| Auth | None (public) |
| Interval | 20 min |
| Charges | Separate API call per booking (charge enrichment) |
| Notes | Reference implementation. Highest volume SWFL county. |

#### Collier County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/collier.py` |
| Class | `CollierCountyScraper` |
| JMS Vendor | Odyssey |
| Base URL | `https://www2.colliersheriff.org` |
| Method | ASP.NET form POST to `/arrestsearch/Report.aspx` |
| Auth | None |
| Interval | 30 min |
| Notes | Uses ViewState form submission. Origin + Referer headers required. |

#### Charlotte County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/charlotte.py` |
| Class | `CharlotteCountyScraper` |
| JMS Vendor | Custom (Revize platform) |
| Base URL | `https://inmates.charlottecountyfl.revize.com` |
| Method | GET `/bookings` → HTML table parsing |
| Auth | None |
| Interval | 45 min |
| Notes | Revize platform. Relative URLs need `BASE_URL` prefix. |

#### Hendry County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/hendry.py` |
| Class | `HendryCountyScraper` |
| JMS Vendor | JailTracker |
| Detail URL | `https://www.hendrysheriff.org/inmateSearch` |
| Method | HTML table parsing with detail pages |
| Auth | None |
| Interval | 120 min |
| Notes | Low volume. Occasional rate limiting. |

#### DeSoto County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/desoto.py` |
| Class | `DeSotoCountyScraper` |
| JMS Vendor | JailTracker |
| Base URL | `https://jail.desotosheriff.org` |
| Method | GET `/DCN/inmates` → HTML parsing with detail links |
| Auth | None |
| Interval | 60 min |
| Notes | Uses `urljoin` for relative URLs (mugshots, detail pages). |

#### Manatee County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/manatee.py` |
| Class | `ManateeCountyScraper` |
| JMS Vendor | New World (Tyler) via Revize |
| Base URL | `https://manatee-sheriff.revize.com` |
| Method | GET `/bookings` → HTML table parsing |
| Auth | None |
| Interval | 45 min |
| Notes | Revize platform (similar to Charlotte). |

#### Sarasota County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/sarasota.py` |
| Class | `SarasotaCountyScraper` |
| JMS Vendor | Odyssey via Revize |
| Base URL | `https://cms.revize.com/revize/apps/sarasota/` |
| Method | Date-based person search + PIN detail lookup |
| Auth | None |
| Interval | 60 min |
| Notes | Two-phase: date search → PIN-based detail fetch. Uses `personSearch.php` and `pinSearch.php`. |

---

### Tier 4 — Central & East FL (6 Counties)

#### Orange County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/orange.py` |
| Class | `OrangeCountyScraper` |
| JMS Vendor | Custom (OCFL BestJail) |
| Base URL | `https://netapps.ocfl.net/BestJail/Home` |
| Method | JSON API — `getInmates`, `getInmateDetails`, `getCharges` |
| Auth | None |
| Interval | 90 min |
| Notes | Clean JSON API. Three-phase: list → details → charges. Referer header required. |

#### Pinellas County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/pinellas.py` |
| Class | `PinellasCountyScraper` |
| JMS Vendor | JailTracker-style |
| Base URL | `https://www.pinellassheriff.gov` |
| Method | GET `/InmateBooking` → HTML parsing with DrissionPage |
| Auth | None |
| Interval | 90 min |
| Notes | Uses DrissionPage (browser automation). Relative detail URLs. |

#### Polk County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/polk.py` |
| Class | `PolkCountyScraper` |
| JMS Vendor | Odyssey |
| Base URL | `https://polksheriff.org` |
| Method | GET `/detention/jail-inquiry` → HTML parsing with DrissionPage |
| Auth | None |
| Interval | 120 min |
| Notes | Jail inquiry page. Relative detail URLs need BASE_URL prefix. |

#### Osceola County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/osceola.py` |
| Class | `OsceolaCountyScraper` |
| JMS Vendor | Custom (Corrections Reports app) |
| Base URL | `https://apps.osceola.org/Apps/CorrectionsReports` |
| Method | Daily report page + detail pages by ID |
| Auth | None |
| Interval | 120 min |
| Notes | Uses `Report/Daily/` for list, `Report/Details/{id}` for individual records. |

#### Seminole County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/seminole.py` |
| Class | `SeminoleCountyScraper` |
| JMS Vendor | Custom (NorthPointe Suite) |
| Base URL | `https://seminole.northpointesuite.com` |
| Method | Custody portal → HTML parsing |
| Auth | None |
| Interval | 90 min |
| Notes | NorthPointe vendor. Portal-style interface. |

#### Palm Beach County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/palm_beach.py` |
| Class | `PalmBeachCountyScraper` |
| JMS Vendor | Custom (PBSO Blotter) |
| Base URL | `https://www3.pbso.org/blotter` |
| Method | ColdFusion app (`index.cfm`) → HTML parsing with DrissionPage |
| Auth | None |
| Interval | 120 min |
| Notes | ColdFusion backend. Relative detail URLs need BASE_URL prefix. |

---

### Tier 5 — Statewide High-Pop (7 Counties)

#### Hillsborough County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/hillsborough.py` |
| Class | `HillsboroughCountyScraper` |
| JMS Vendor | New World (HCSO custom) |
| Search URL | `https://webapps.hcso.tampa.fl.us/arrestinquiry/Home/Search` |
| Method | DrissionPage browser automation — search + pagination |
| Auth | **Requires HCSO credentials** (`HCSO_EMAIL`, `HCSO_PASSWORD` env vars) |
| Interval | 90 min |
| Notes | **Only county requiring login.** Pagination via next button class detection. |

#### Broward County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/broward.py` |
| Class | `BrowardCountyScraper` |
| JMS Vendor | Custom (BSO ArrestSearch) |
| Base URL | `https://apps.sheriff.org` |
| Detail URL | `https://apps.sheriff.org/ArrestSearch/InmateDetail` |
| Method | ArrestSearch app → HTML parsing |
| Auth | None |
| Interval | 60 min |
| Notes | BSO's custom arrest search application. High volume. |

#### Duval County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/duval.py` |
| Class | `DuvalCountyScraper` |
| JMS Vendor | Custom (JSO Inmate Search) |
| Base URL | `https://inmatesearch.jaxsheriff.org/` |
| Method | DrissionPage browser automation |
| Auth | None |
| Interval | 90 min |
| Notes | Jacksonville Sheriff's Office custom portal. |

#### Volusia County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/volusia.py` |
| Class | `VolusiaCountyScraper` |
| JMS Vendor | Custom (VCSO) |
| Base URL | `https://vcso.us/jail-info/inmate-search/` |
| Method | DrissionPage browser automation |
| Auth | None |
| Interval | 90 min |
| Notes | Volusia County Sheriff's Office WordPress-based. |

#### Brevard County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/brevard.py` |
| Class | `BrevardCountyScraper` |
| JMS Vendor | Odyssey |
| Search URL | `https://www.brevardcounty.us/JailCompliance/SubSearch` |
| Method | POST form with `LastName` letter iteration (A-Z) |
| Auth | None |
| Interval | 120 min |
| Notes | Alphabetical search — iterates 26 letters. Higher latency. |

#### Pasco County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/pasco.py` |
| Class | `PascoCountyScraper` |
| JMS Vendor | Custom |
| Base URL | `https://www.pascosheriff.com/inmate-search.html` |
| Method | DrissionPage browser automation |
| Auth | None |
| Interval | 90 min |
| Notes | Static HTML page with dynamic content loading. |

#### Escambia County ✅
| Property | Value |
|----------|-------|
| File | `scrapers/counties/escambia.py` |
| Class | `EscambiaCountyScraper` |
| JMS Vendor | Odyssey (via myescambia.com) |
| Search URL | `https://myescambia.com/our-services/corrections/inmate-lookup` |
| Method | GET search → form action detection → HTML parsing |
| Auth | None |
| Interval | 120 min |
| Notes | Form action URL may change; scraper auto-detects from page. |

---

## Adding a County

See `.agent/skills/scraper-builder/SKILL.md` for the full workflow.

Key steps:
1. Research the county's JMS vendor (see `docs/COUNTY_REGISTRY.md`)
2. Create `scrapers/counties/{county}.py` inheriting from `BaseScraper`
3. Implement `scrape_arrests()` method
4. Register in `main.py` scheduler with appropriate interval
5. Test with `python main.py {county_name}` CLI flag
6. Monitor first 24h of production data
7. Update `docs/COUNTY_REGISTRY.md`: mark ✅ Active

---

## Debugging

See `.agent/skills/scraper-debugger/SKILL.md` for the systematic approach.

### Common Failure Modes

| Failure | Symptoms | Fix |
|---------|----------|-----|
| URL changed | 404 / 301 redirect | Update BASE_URL in county scraper |
| Anti-bot block | 403 / Cloudflare challenge | Rotate UA, add delays, use DrissionPage |
| HTML changed | Parse errors, missing fields | Update selectors/parsing logic |
| SSL error | Certificate expired | Verify URL, may need cert bypass |
| Rate limited | 429 / connection reset | Increase interval in scheduler |
| Login expired | 401/403 (Hillsborough only) | Rotate HCSO credentials |

---

## Constraints

- The scraper agent **only creates ArrestLead records**
- It does NOT create Defendants, Matches, or BondCases
- It does NOT trigger paperwork or signatures
- Score is informational — it does not control downstream workflow
- All writes are idempotent: `County + Booking_Number` dedup key
- Never scrape at intervals < 20 minutes (Lee is the fastest at 20min)
- Always respect rate limits and add delays between detail page requests
