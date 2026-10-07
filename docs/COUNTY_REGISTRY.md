# 🗺️ Florida County Registry — All 67 Counties
> Master reference for every Florida county jail roster. Updated as scrapers are built and validated.
> **Last Updated:** 2026-10-07 (SmartWEB paging, idle-eight, SmartWEB-five) | Original body largely 2026-08-04 | **Active Scrapers:** **67 FL** (full state on `REGISTERED_COUNTIES` + scheduler) · multi-state total **361** (FL **67**) — see root `STATUS.md`. **Architecture note:** FL uses custom scrapers + shared APE proxy / SmartWEB JAIL View helper — not wholesale multi-state platform wrappers. Registration is not `verified_public`.

---

## Legend
| Status | Meaning |
|--------|---------|
| ✅ Active | Scraper running in production (Hetzner VPS, registered in `main.py`) |
| 🔄 Building | Scraper file exists, not yet validated / commented out |
| 🔵 Validated | URL confirmed, scraper not yet built |
| 🟡 Needs Recon | URL unconfirmed, needs manual investigation |
| 🔴 Blocked | Anti-bot, reCAPTCHA, or no public roster |

---

## Tier 1 — SWFL Core (7 Counties)
| # | County | JMS / Method | Scraper File | Status | Interval | Last Verified |
|---|--------|-------------|--------------|--------|----------|---------------|
| 1 | **Lee** | curl_cffi GET + origin DNS pin (`lee_origin`) + durable cooldown + honest empty/error — sheriffleefl.org public-api | `lee.py` | ✅ Active | 30 min | 2026-09-23 (Mac write smoke 53 ok; self-heal branch) |
| 2 | **Collier** | Odyssey REST API | `collier.py` | ✅ Active | 15 min | 2026-04-27 |
| 3 | **Charlotte** | Patchright + Warren APE (sticky) / office SOCKS — Revize CF; **exit-IP preflight** rejects Datacamp/VPN/NordVPN; APE Warren sticky or office SOCKS required | `charlotte.py` | ⚠️ Contract **unverified** (Health default) — Revize roster may emit only with healthy US residential egress; **not** `verified_public`; reopen blocked pending Brendan residential smoke — see `docs/recon/SWFL_SOURCE_CONTRACT_QUEUE.md` | 90 min | Code last exercised 2026-07-16; 2026-09-22 recon: no new live validation this pass |
| 4 | **Manatee** | Same as Charlotte (Revize + residential preflight; sticky `fl-manatee`) | `manatee.py` | ⚠️ Contract **unverified** (Health default) — same residential gate as Charlotte; **not** `verified_public`; do not treat Charlotte success as Manatee proof — see `docs/recon/SWFL_SOURCE_CONTRACT_QUEUE.md` | 75 min | Code last exercised 2026-07-16; 2026-09-22 recon: no new live validation this pass |
| 5 | **Sarasota** | Official Sarasota booking-safe broad roster not verified through normal public access. Third-party mirror, CAPTCHA/JailTracker, proxy, profile, and sensitive-field paths are retired. | `sarasota.py` | ⏳ **Fail closed** (`SOURCE_CONTRACT_VALIDATED=False` + Health `fail_closed`) — reopen requires official broad roster with identity + source booking id + booking timestamp; see `docs/recon/SWFL_SOURCE_CONTRACT_QUEUE.md` | 90 min | Guard deployed 2026-08-14 (run `31843789326`); 2026-09-22 recon: still no verified official contract; no write/alert claim |
| 6 | **DeSoto** | DevExpress grid (DrissionPage) — **not** JailTracker | `desoto.py` | ✅ Active | 60 min | 2026-07-16 |
| 7 | **Hendry** | Official OCV S3 `inmates.json` | `hendry.py` | ✅ Active (v4 OCV) | 120 min | 2026-07-10 |

---

## Tier 2 — Tampa Bay / I-4 Corridor (8 Counties)
| # | County | JMS / Method | Scraper File | Status | Interval | Last Verified |
|---|--------|-------------|--------------|--------|----------|---------------|
| 8 | **Hillsborough** | httpx direct-first + reCAPTCHA + SOLVECAPTCHA (HCSO login) | `hillsborough.py` | ✅ Active (needs HCSO_* + SOLVECAPTCHA_KEY) | 90 min | 2026-07-24 |
| 9 | **Pinellas** | DrissionPage — date search | `pinellas.py` | ✅ Active | 90 min | 2026-04-27 |
| 10 | **Seminole** | Custom | `seminole.py` | ✅ Active | 90 min | 2026-04-27 |
| 11 | **Orange** | requests GET — getInmates API | `orange.py` | ✅ Active | 90 min | 2026-04-27 |
| 12 | **Pasco** | DrissionPage — Cloudflare bypass | `pasco.py` | ✅ Active | 90 min | 2026-04-27 |
| 13 | **Lake** | requests POST `recent_data` + Turnstile token (SolveCaptcha, owner-approved; shared `scrapers/solvecaptcha.py`) | `lake.py` | ✅ `verified_public` (Mac write smoke 17 new; needs SOLVECAPTCHA_KEY) | 90 min | 2026-09-25 |
| 14 | **Hernando** | Custom HTML | `hernando.py` | ✅ Active | 90 min | 2026-04-27 |
| 15 | **Citrus** | Public recent-arrest PDF (source `AR #`) — plain requests + pdfplumber | `citrus.py` | ✅ Active (unverified until write smoke) | 120 min | 2026-10-07 |

---

## Tier 3 — Central FL / Heartland (6 Counties)
| # | County | JMS / Method | Scraper File | Status | Interval | Last Verified |
|---|--------|-------------|--------------|--------|----------|---------------|
| 16 | **Polk** | Direct Kendo UI REST API | `polk.py` | ✅ Active | 120 min | 2026-05-24 |
| 17 | **Osceola** | DrissionPage — daily reports | `osceola.py` | ✅ Active | 120 min | 2026-04-27 |
| 18 | **Sumter** | SmartWEB JAIL View (`SCSO<YY>JBN######`, modern AddMoreResults) — plain requests | `sumter.py` | ✅ Active (unverified until write smoke) | 180 min | 2026-10-07 |
| 19 | **Highlands** | Direct OCV JSON API | `highlands.py` | ✅ Active | 120 min | 2026-05-24 |
| 20 | **Glades** | JailTracker | `glades.py` | ✅ Active | 180 min | 2026-04-27 |
| 21 | **Hardee** | Stub — `hardee.py` returns no rows; hardeeso.com inmate search links only the OCV mobile app; OCV `inmates.json` buckets return 403 | `hardee.py` | 🔴 No public web roster (hold) | 120 min | 2026-10-07 |

---

## Tier 4 — Southeast / Treasure Coast (6 Counties)
| # | County | JMS / Method | Scraper File | Status | Interval | Last Verified |
|---|--------|-------------|--------------|--------|----------|---------------|
| 22 | **Palm Beach** | DrissionPage — PBSO ColdFusion blotter | `palm_beach.py` | ✅ Active (fixed page.html 2026-07-10) | 120 min | 2026-07-10 |
| 23 | **Broward** | Official BSO arrest search | `broward.py` | ✅ Live — Turnstile Arrest Search name-prefix + paged grid; Mac smoke 2026-09-23 (30 new / status=ok; action=arrest_search); needs `SOLVECAPTCHA_KEY` | 60 min | Writes enabled after contract validation |
| 24 | **Martin** | Direct Tyler Technologies REST API | `martin.py` | ✅ Active | 120 min | 2026-05-24 |
| 25 | **St. Lucie** | requests POST — PHP table | `st_lucie.py` | ✅ Active | 90 min | 2026-04-27 |
| 26 | **Indian River** | requests GET — BS4 card list | `indian_river.py` | ✅ Active | 120 min | 2026-04-27 |
| 27 | **Okeechobee** | Wix shell page — no public data source | `okeechobee.py` | 🔴 No public roster URL | 120 min | 2026-07-24 |

---

## Tier 5 — East Coast / Space Coast (3 Counties)
| # | County | JMS / Method | Scraper File | Status | Interval | Last Verified |
|---|--------|-------------|--------------|--------|----------|---------------|
| 28 | **Volusia** | Direct ASP.NET Postback (volusiamug.vcgov.org) | `volusia.py` | ✅ Active | 90 min | 2026-05-24 |
| 29 | **Brevard** | Odyssey REST API | `brevard.py` | ✅ Active | 120 min | 2026-04-27 |
| 30 | **Flagler** | New World HTML | `flagler.py` | ✅ Active | 120 min | 2026-04-27 |

---

## Tier 6 — North Central FL (5 Counties)
| # | County | JMS / Method | Scraper File | Status | Interval | Last Verified |
|---|--------|-------------|--------------|--------|----------|---------------|
| 31 | **Alachua** | Public View All grid has names/Book Date only — no source booking ID (person-level MNI only) | `alachua.py` | 🔴 Fail closed (no invented name keys) | 90 min | 2026-10-07 |
| 32 | **Putnam** | SmartWEB JAIL View (`PCSO<YY>JBN######`, legacy AddMoreResults) — plain requests | `putnam.py` | ✅ Active (unverified until write smoke) | 180 min | 2026-10-07 |
| 33 | **Columbia** | Legacy SmartWEB IP returns 503; no replacement public roster URL | `columbia.py` | 🔴 Fail closed (`SOURCE_CONTRACT_VALIDATED=False`) | 120 min | 2026-10-07 |
| 34 | **Suwannee** | SmartWEB JAIL View (`SCSO<YY>JBN######`, modern AddMoreResults) — plain requests | `suwannee.py` | ✅ `verified_public` (Mac write smoke 44 new) | 180 min | 2026-09-25 |
| 35 | **Marion** | curl_cffi + **required residential** (Warren/Tailscale) — jail.marionso.com AWS WAF | `marion.py` | ✅ Active (residential egress) | 90 min | 2026-08-04 |

> **Note:** Marion fails closed without US residential exit (APE Warren / Tailscale SOCKS). Direct VPS IP is always 403.

---

## Tier 7 — NE FL / First Coast (4 Counties)
| # | County | JMS / Method | Scraper File | Status | Interval | Last Verified |
|---|--------|-------------|--------------|--------|----------|---------------|
| 36 | **Duval** | DrissionPage — API interception (jaxsheriff.org) | `duval.py` | ✅ Active | 90 min | 2026-04-27 |
| 37 | **St. Johns** | Stub — `st_johns.py` returns no rows; sjso.org links `/smartwebclient/jail.aspx` but it answers **403** (Cloudflare/nginx) to ordinary access | `st_johns.py` | 🔴 No reachable public roster (hold; no WAF bypass) | 120 min | 2026-10-07 |
| 38 | **Nassau** | New World InmateInquiry GET | `nassau.py` | ✅ Active | 120 min | 2026-04-27 |
| 39 | **Clay** | Public detention listing has Name/Booking Date only — no source booking ID | `clay.py` | 🔴 Fail closed (no invented name keys) | 120 min | 2026-10-07 |

---

## Tier 8 — Panhandle (7 Counties)
| # | County | JMS / Method | Scraper File | Status | Interval | Last Verified |
|---|--------|-------------|--------------|--------|----------|---------------|
| 40 | **Escambia** | SmartWEB JAIL View (`ECC<YY>JBN######`) — plain requests | `escambia.py` | ✅ Active (unverified until write smoke) | 120 min | 2026-10-07 |
| 41 | **Okaloosa** | Inmate Locator `Default.aspx` (source Booking# 10-digit) — plain requests A–Z | `okaloosa.py` | ✅ Active (unverified until write smoke) | 120 min | 2026-10-07 |
| 42 | **Bay** | Custom HTML | `bay.py` | ✅ Active | 120 min | 2026-04-27 |
| 43 | **Santa Rosa** | SmartWEB JAIL View (`SRSO<YY>JBN######`) — plain requests | `santa_rosa.py` | ✅ Active (unverified until write smoke) | 120 min | 2026-10-07 |
| 44 | **Walton** | New World InmateInquiry GET | `walton.py` | ✅ Active | 120 min | 2026-04-27 |
| 45 | **Jackson** | Stub — no public roster | `jackson.py` | ✅ Active | 360 min | 2026-04-27 |
| 46 | **Gadsden** | SmartWEB iframe → `69.21.72.195` (server dead) | `gadsden.py` | 🔴 Upstream dead | 180 min | 2026-07-24 |

---

## Tier 9 — North FL / Rural (4 Counties)
| # | County | JMS / Method | Scraper File | Status | Interval | Last Verified |
|---|--------|-------------|--------------|--------|----------|---------------|
| 47 | **Leon** | requests POST — A-Z iteration | `leon.py` | 🔴 Broken Target (500 Error) | 90 min | 2026-05-24 |
| 48 | **Taylor** | SmartWEB JAIL View (`TCSO<YY>JBN######`) — plain requests | `taylor.py` | ✅ Active (unverified until write smoke) | 240 min | 2026-10-07 |
| 49 | **Dixie** | SmartWEB JAIL View (`DCSO<YY>JBN######`) — plain requests | `dixie.py` | ✅ Active (unverified until write smoke) | 240 min | 2026-10-07 |
| 50 | **Monroe** | JSON API `data.keysso.net/api/arrests` (v2) | `monroe.py` | ✅ Active | 120 min | 2026-07-24 |

---

## Statewide Scaffold Coverage and Source Recon

**All 67 Florida counties now have a local module, a unique `County (FL)` dashboard label, and a runtime scheduler registration.** That operational scaffold is intentionally separate from source-contract validation: registration alone must never be represented as a successful county ingest, Mongo write, or alert.

### Miami-Dade
| County | Module | Runtime state | Source posture |
|---|---|---|---|
| **Miami-Dade** | `miami_dade.py` | Registered | ArcGIS FeatureServer parser deployed 2026-08-14. It requires the official source identifier and booking date; county-specific Mongo and alert telemetry remain unproven. |

### Rural source-recon queue — scaffolded and registered

The following counties are **not missing implementations**. Their modules and scheduler entries are present, but public source contracts still require county-by-county confirmation before any record-emitting behavior can be relied on: **Wakulla, Baker, Levy, Lafayette, Union, Calhoun, Gulf, Holmes, Jefferson, Liberty, Washington, and Franklin** (Bradford / Hamilton / Madison / Gilchrist SmartWEB contracts proven 2026-10-07 — Health unverified until write smoke).

> A scaffold must return no arrest records whenever a source row lacks a complete identity and a source-issued immutable booking identifier. Do not synthesize a key from a name, date, profile URL, or document ID. Record the final source decision in `SCRAPER_SOURCE_STATES` and this registry only after a bounded validation.

---

## Miami-Dade Recon Notes

The MDCR inmate search uses a DevExpress ASP.NET app with **Google reCAPTCHA v2** on every search, making form POST automation infeasible without a CAPTCHA-solving service.

**Recommended approach — ArcGIS Open Data polling:**
```
Dataset ID: c2275711ced240c6bc4e998ee1910e85
Hub URL:    https://gis-mdc.opendata.arcgis.com/datasets/c2275711ced240c6bc4e998ee1910e85/about
Note:       opendata.miamidade.gov now redirects to hub.arcgis.com (legacy Socrata gone)
Update freq: Daily (not real-time)
Approach:   Query the anonymous FeatureServer directly with `ObjectId,GlobalID,BookDate,Defendant,Charge1,Charge3`; exclude address, ZIP, and DOB fields. Fail closed without a complete name, source key, or booking date. The source is date-granular, so it must not fabricate a booking time or custody status.
```

---

## Captcha OCR Stack (JailTracker)

> File: `scrapers/captcha_ocr.py` · Wired into `scrapers/jailtracker_base.py`
> CLI bench: `python -m scrapers.captcha_ocr <image.png> [--answers ANSWER] [--engines ddddocr,tesseract]`

### Engine Priority (all optional — soft-skip on missing deps)

| Priority | Engine | Install | Notes |
|----------|--------|---------|-------|
| 1 | **ddddocr** | `pip install ddddocr` | Captcha-specialized; always-on when installed |
| 2 | **Tesseract** | `apt install tesseract-ocr` (in Dockerfile) | CLI-based, no Python dep; PSM 7/8/13 variants |
| 3 | **PaddleOCR** | `pip install -r requirements-ocr-extra.txt` | Heavy; disable with `CAPTCHA_OCR_PADDLE=0` |
| 4 | **EasyOCR** | Same as above | Heavy; disable with `CAPTCHA_OCR_EASYOCR=0` |
| 5 | **SolveCaptcha** | `SOLVECAPTCHA_KEY` env var | Paid fallback (~$0.50/1000); kicks in after local OCR exhausted |
| 6 | **OpenAI GPT-4o** | `OPENAI_API_KEY` env var | Last resort paid fallback |

**Env overrides:** `CAPTCHA_OCR_ENGINES=ddddocr,tesseract` (comma-separated) overrides engine selection globally.

### Bench Results (2026-07-17, `scratch/sarasota_captcha.png`, answer=`WLKd`)

| Stack | Best Guess | Result | Notes |
|-------|-----------|--------|-------|
| ddddocr only | `Wkd` | ✗ | Missed `L` — 3-char read of 4-char captcha |
| ddddocr + tesseract | `WLka` | ✗ | All 4 chars found; `a` vs `d` confusion (case-perm covers case, not char errors) |
| + SolveCaptcha/OpenAI | `WLKd` | ✓ | Paid solver closes char-level confusion |

**Case-permutation multi-try:** JailTracker is case-sensitive but wrong codes keep the same `captchaKey`. Up to 48 case variants are tried per captcha image via `POST /Captcha/validatecaptcha` before consuming the key. Covers case errors; paid solver covers char-level errors.

### FL JailTracker `POST /Offender` 400 — Known Issue

> **Do not re-probe FL JT agencies aggressively.** The captcha is solvable; the agency backend rejects the roster POST with an empty HTTP 400. Confirmed for `SARASOTA_COUNTY_FL`, `MANATEE_COUNTY_FL`, and `CHARLOTTE_COUNTY_FL`. SC/GA agencies (Greenwood ~210, Chester ~104) work correctly with the same flow. **`sarasota.py` no longer uses JailTracker as a fallback** — it fail-closes with `SOURCE_CONTRACT_VALIDATED=False` until an official booking-safe broad roster is validated. Charlotte/Manatee use Revize+residential, not JT.

---
## JMS Vendor Scraping Patterns

### Odyssey (Tyler Technologies)
- **Pattern**: REST API with JSON responses
- **Auth**: None (public inmate search)
- **Pagination**: Offset-based (`?page=1&size=50`)
- **Active Counties**: Lee, Collier, Brevard. Escambia moved to SmartWEB JAIL View (2026-10-07). Sarasota is fail_closed.

### JailTracker (Black Creek ISC)
- **Pattern**: Paginated HTML tables or JSON API
- **Auth**: None; occasional CAPTCHA / rate limiting
- **Active Counties**: Highlands, Glades. Citrus is the sheriff PDF roster, not JailTracker (2026-10-07). Baker/Calhoun/Gulf/Holmes/Levy/Wakulla/Washington stay fail_closed.

### New World / InmateInquiry (Tyler Technologies)
- **Pattern**: Server-rendered HTML listing + detail pages (GET)
- **Active Counties**: Hillsborough, Nassau, Walton, Flagler

### SmartWeb (Black Creek ISC)
- **Pattern**: ASP.NET POST form with ViewState, returns HTML table
- **Active Counties**: Putnam, Suwannee, Santa Rosa, Sumter, Taylor, Bradford, Dixie, Escambia, Hamilton, Madison, Gilchrist. Health stays unverified until write smoke except Suwannee (`verified_public`).

### DrissionPage (Browser Automation)
- **Pattern**: Chromium headless — JS rendering or Cloudflare bypass required
- **Active Counties**: Charlotte, Palm Beach, Volusia, Duval, Pasco, Pinellas, Polk, Osceola, Lake, Manatee, Sarasota, Martin

### Custom / In-House
- **Pattern**: Varies — GET requests, HTML parsing, API reverse-engineering
- **Active Counties**: Orange, Seminole, St. Lucie, Indian River, Bay, Monroe, Hernando. Fail closed or held, not active emitters: Okeechobee, Columbia, Clay, Gadsden, Leon, St. Johns, Hardee, Alachua. Dixie and Okaloosa are listed under SmartWEB / Inmate Locator above. Broward is live (`verified_public`) on the Turnstile Arrest Search path since 2026-09-23; sequential identifier probing remains prohibited.

---

## Self-Healing URL Patterns
| Vendor | Common URL Pattern | Fallback Pattern |
|--------|-------------------|------------------|
| Odyssey | `https://[county]sheriff.org/api/inmates` | `https://[county].tylerhost.net/api/inmates` |
| JailTracker | `https://omsweb.public-safety-cloud.com/jtclientweb/jailtracker/index/[ID]` | Google: `site:public-safety-cloud.com [county]` |
| New World | `https://[county]sheriff.org/inmates` | Check for `InmateInquiry` path |
| SmartWeb | `https://smartcop.[county]sheriff.org/smartwebclient/Jail.aspx` | `https://smartweb.[county]so.net/SmartWebClient/jail.aspx` |
| Custom | `https://[county]sheriff.org/inmate-search` | Google: `[county] florida sheriff inmate search` |

**HTTPS Migration Note**: Many counties migrate from HTTP to HTTPS without redirect. Always try HTTPS first.

---

## Adding a New County
See `.agent/workflows/add-county-scraper.md` for the detailed procedure.

```bash
# 1. Recon: Find roster URL → Identify JMS vendor
# 2. Copy closest template scraper
# 3. Adapt parsing logic
# 4. Test: python main.py <county_name>
# 5. Register in main.py with interval
# 6. Update this file: mark Active
```
