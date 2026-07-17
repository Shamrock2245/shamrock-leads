# 🗺️ Florida County Registry — All 67 Counties
> Master reference for every Florida county jail roster. Updated as scrapers are built and validated.
> **Last Updated:** 2026-07-17 | **Active Scrapers:** ~51 | **Architecture note:** FL uses custom scrapers + shared APE proxy / SmartWeb card parser — not wholesale multi-state platform wrappers. See plan: FL APE + SmartWeb quality first.

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
| 1 | **Lee** | curl_cffi GET — sheriffleefl.org | `lee.py` | ✅ Active | 10 min | 2026-04-27 |
| 2 | **Collier** | Odyssey REST API | `collier.py` | ✅ Active | 15 min | 2026-04-27 |
| 3 | **Charlotte** | Patchright + Warren APE (sticky) / office SOCKS — Revize CF; **exit-IP preflight** rejects Datacamp/VPN/NordVPN; APE Warren sticky or office SOCKS required | `charlotte.py` | ⚠️ Needs **US residential** exit (Warren APE sticky or office SOCKS) | 90 min | 2026-07-16 |
| 4 | **Manatee** | Same as Charlotte | `manatee.py` | ⚠️ Needs **US residential** exit | 75 min | 2026-07-16 |
| 5 | **Sarasota** | **mugshotssarasota.com WP API** (primary, ~189 records/7d, ~7s, no proxy needed); JT FL `POST /Offender` returns empty 400 after valid captcha — agency backend issue, not OCR (do not re-probe); Revize CF hard-blocked on residential+proxy | `sarasota.py` | ✅ Active (v5 mugshots — **smoke-tested 2026-07-17, 189 records**) | 90 min | 2026-07-17 |
| 6 | **DeSoto** | DevExpress grid (DrissionPage) — **not** JailTracker | `desoto.py` | ✅ Active | 60 min | 2026-07-16 |
| 7 | **Hendry** | Official OCV S3 `inmates.json` | `hendry.py` | ✅ Active (v4 OCV) | 120 min | 2026-07-10 |

---

## Tier 2 — Tampa Bay / I-4 Corridor (8 Counties)
| # | County | JMS / Method | Scraper File | Status | Interval | Last Verified |
|---|--------|-------------|--------------|--------|----------|---------------|
| 8 | **Hillsborough** | httpx + reCAPTCHA + **APE/SOCKS** (HCSO login) | `hillsborough.py` | ✅ Active (needs HCSO_* + SOLVECAPTCHA_KEY) | 90 min | 2026-07-16 |
| 9 | **Pinellas** | DrissionPage — date search | `pinellas.py` | ✅ Active | 90 min | 2026-04-27 |
| 10 | **Seminole** | Custom | `seminole.py` | ✅ Active | 90 min | 2026-04-27 |
| 11 | **Orange** | requests GET — getInmates API | `orange.py` | ✅ Active | 90 min | 2026-04-27 |
| 12 | **Pasco** | DrissionPage — Cloudflare bypass | `pasco.py` | ✅ Active | 90 min | 2026-04-27 |
| 13 | **Lake** | DrissionPage — JS SPA | `lake.py` | ✅ Active | 90 min | 2026-04-27 |
| 14 | **Hernando** | Custom HTML | `hernando.py` | ✅ Active | 90 min | 2026-04-27 |
| 15 | **Citrus** | JailTracker | `citrus.py` | ✅ Active | 120 min | 2026-04-27 |

---

## Tier 3 — Central FL / Heartland (6 Counties)
| # | County | JMS / Method | Scraper File | Status | Interval | Last Verified |
|---|--------|-------------|--------------|--------|----------|---------------|
| 16 | **Polk** | Direct Kendo UI REST API | `polk.py` | ✅ Active | 120 min | 2026-05-24 |
| 17 | **Osceola** | DrissionPage — daily reports | `osceola.py` | ✅ Active | 120 min | 2026-04-27 |
| 18 | **Sumter** | SmartWeb ASP.NET POST | `sumter.py` | ✅ Active | 180 min | 2026-04-27 |
| 19 | **Highlands** | Direct OCV JSON API | `highlands.py` | ✅ Active | 120 min | 2026-05-24 |
| 20 | **Glades** | JailTracker | `glades.py` | ✅ Active | 180 min | 2026-04-27 |
| 21 | **Hardee** | OCV API | `hardee.py` | ✅ Active | 120 min | 2026-04-27 |

---

## Tier 4 — Southeast / Treasure Coast (6 Counties)
| # | County | JMS / Method | Scraper File | Status | Interval | Last Verified |
|---|--------|-------------|--------------|--------|----------|---------------|
| 22 | **Palm Beach** | DrissionPage — PBSO ColdFusion blotter | `palm_beach.py` | ✅ Active (fixed page.html 2026-07-10) | 120 min | 2026-07-10 |
| 23 | **Broward** | HTTP GET — sequential ID probe | `broward.py` | ✅ Active | 60 min | 2026-04-27 |
| 22 | **Martin** | Direct Tyler Technologies REST API | `martin.py` | ✅ Active | 120 min | 2026-05-24 |
| 25 | **St. Lucie** | requests POST — PHP table | `st_lucie.py` | ✅ Active | 90 min | 2026-04-27 |
| 26 | **Indian River** | requests GET — BS4 card list | `indian_river.py` | ✅ Active | 120 min | 2026-04-27 |
| 27 | **Okeechobee** | requests GET — HTML table | `okeechobee.py` | ✅ Active | 120 min | 2026-04-27 |

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
| 31 | **Alachua** | Custom HTML | `alachua.py` | ✅ Active | 90 min | 2026-04-27 |
| 32 | **Putnam** | SmartWeb — wildcard (%) search + AJAX AddMoreResults | `putnam.py` | ✅ Active | 180 min | 2026-05-25 |
| 33 | **Columbia** | P2C HTML | `columbia.py` | ✅ Active | 120 min | 2026-04-27 |
| 34 | **Suwannee** | SmartWeb — wildcard (%) search + AJAX AddMoreResults | `suwannee.py` | ✅ Active | 180 min | 2026-05-25 |
| 35 | **Marion** | requests POST — jail.marionso.com | `marion.py` | 🔄 Building | — | 2026-04-27 |

> **Note:** Marion is commented out in `main.py`. Scraper file exists and needs validation before re-enabling.

---

## Tier 7 — NE FL / First Coast (4 Counties)
| # | County | JMS / Method | Scraper File | Status | Interval | Last Verified |
|---|--------|-------------|--------------|--------|----------|---------------|
| 36 | **Duval** | DrissionPage — API interception (jaxsheriff.org) | `duval.py` | ✅ Active | 90 min | 2026-04-27 |
| 37 | **St. Johns** | requests GET — BS4 HTML table | `st_johns.py` | ✅ Active | 120 min | 2026-04-27 |
| 38 | **Nassau** | New World InmateInquiry GET | `nassau.py` | ✅ Active | 120 min | 2026-04-27 |
| 39 | **Clay** | Custom HTML | `clay.py` | ✅ Active | 120 min | 2026-04-27 |

---

## Tier 8 — Panhandle (7 Counties)
| # | County | JMS / Method | Scraper File | Status | Interval | Last Verified |
|---|--------|-------------|--------------|--------|----------|---------------|
| 40 | **Escambia** | Odyssey REST API | `escambia.py` | ✅ Active | 120 min | 2026-04-27 |
| 41 | **Okaloosa** | requests POST — HTML table | `okaloosa.py` | ✅ Active | 120 min | 2026-04-27 |
| 42 | **Bay** | Custom HTML | `bay.py` | ✅ Active | 120 min | 2026-04-27 |
| 43 | **Santa Rosa** | SmartWeb — wildcard (%) search + AJAX AddMoreResults | `santa_rosa.py` | ✅ Active | 120 min | 2026-05-25 |
| 44 | **Walton** | New World InmateInquiry GET | `walton.py` | ✅ Active | 120 min | 2026-04-27 |
| 45 | **Jackson** | Stub — no public roster | `jackson.py` | ✅ Active | 360 min | 2026-04-27 |
| 46 | **Gadsden** | Custom — needs recon | `gadsden.py` | ✅ Active | 180 min | 2026-04-27 |

---

## Tier 9 — North FL / Rural (4 Counties)
| # | County | JMS / Method | Scraper File | Status | Interval | Last Verified |
|---|--------|-------------|--------------|--------|----------|---------------|
| 47 | **Leon** | requests POST — A-Z iteration | `leon.py` | 🔴 Broken Target (500 Error) | 90 min | 2026-05-24 |
| 48 | **Taylor** | SmartWeb ASP.NET POST | `taylor.py` | ✅ Active | 240 min | 2026-04-27 |
| 49 | **Dixie** | Custom HTML | `dixie.py` | ✅ Active | 240 min | 2026-04-27 |
| 50 | **Monroe** | curl_cffi POST — disclaimer bypass | `monroe.py` | ✅ Active | 120 min | 2026-04-27 |

---

## Priority Build — Not Yet Scraped (17 Counties)

### High Priority — Miami-Dade (Largest County in FL)
| # | County | JMS / Method | Status | Notes |
|---|--------|-------------|--------|-------|
| 51 | **Miami-Dade** | ArcGIS FeatureServer (code in `miami_dade.py`) | ✅ Path exists | Prefer ArcGIS open data over reCAPTCHA portal. Validate prod scrape. Portal search still reCAPTCHA-blocked. |

### Needs Recon (16 Counties)
| # | County | Status | Notes |
|---|--------|--------|-------|
| 52 | Wakulla | 🟡 Needs Recon | Small rural, Tallahassee area |
| 53 | Baker | 🟡 Needs Recon | Small rural, NE FL |
| 54 | Bradford | 🟡 Needs Recon | Small rural, NE FL |
| 55 | Levy | 🟡 Needs Recon | Nature Coast |
| 56 | Hamilton | 🟡 Needs Recon | Rural North FL |
| 57 | Lafayette | 🟡 Needs Recon | Rural North FL — very small |
| 58 | Madison | 🟡 Needs Recon | Rural North FL |
| 59 | Gilchrist | 🟡 Needs Recon | Rural North FL |
| 60 | Union | 🟡 Needs Recon | Rural North FL |
| 61 | Calhoun | 🟡 Needs Recon | Rural Panhandle |
| 62 | Gulf | 🟡 Needs Recon | Rural Panhandle |
| 63 | Holmes | 🟡 Needs Recon | Rural Panhandle |
| 64 | Jefferson | 🟡 Needs Recon | Rural North FL |
| 65 | Liberty | 🟡 Needs Recon | Rural Panhandle — smallest county |
| 66 | Washington | 🟡 Needs Recon | Rural Panhandle |
| 67 | Franklin | 🟡 Needs Recon | Rural Panhandle |

---

## Miami-Dade Recon Notes

The MDCR inmate search uses a DevExpress ASP.NET app with **Google reCAPTCHA v2** on every search, making form POST automation infeasible without a CAPTCHA-solving service.

**Recommended approach — ArcGIS Open Data polling:**
```
Dataset ID: c2275711ced240c6bc4e998ee1910e85
Hub URL:    https://gis-mdc.opendata.arcgis.com/datasets/c2275711ced240c6bc4e998ee1910e85/about
Note:       opendata.miamidade.gov now redirects to hub.arcgis.com (legacy Socrata gone)
Update freq: Daily (not real-time)
Approach:   Download GeoJSON/CSV snapshot, diff vs last snapshot, ingest new bookings
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

> **Do not re-probe FL JT agencies aggressively.** The captcha is solvable; the agency backend rejects the roster POST with an empty HTTP 400. Confirmed for `SARASOTA_COUNTY_FL`, `MANATEE_COUNTY_FL`, and `CHARLOTTE_COUNTY_FL`. SC/GA agencies (Greenwood ~210, Chester ~104) work correctly with the same flow. FL JT is kept as a fallback in `sarasota.py` — it will auto-recover if the agency backend is fixed.

---
## JMS Vendor Scraping Patterns

### Odyssey (Tyler Technologies)
- **Pattern**: REST API with JSON responses
- **Auth**: None (public inmate search)
- **Pagination**: Offset-based (`?page=1&size=50`)
- **Active Counties**: Lee, Collier, Sarasota, Brevard, Escambia

### JailTracker (Black Creek ISC)
- **Pattern**: Paginated HTML tables or JSON API
- **Auth**: None; occasional CAPTCHA / rate limiting
- **Active Counties**: DeSoto, Hendry, Citrus, Highlands, Glades

### New World / InmateInquiry (Tyler Technologies)
- **Pattern**: Server-rendered HTML listing + detail pages (GET)
- **Active Counties**: Hillsborough, Nassau, Walton, Flagler

### SmartWeb (Black Creek ISC)
- **Pattern**: ASP.NET POST form with ViewState, returns HTML table
- **Active Counties**: Putnam, Suwannee, Santa Rosa, Sumter, Taylor

### DrissionPage (Browser Automation)
- **Pattern**: Chromium headless — JS rendering or Cloudflare bypass required
- **Active Counties**: Charlotte, Palm Beach, Volusia, Duval, Pasco, Pinellas, Polk, Osceola, Lake, Manatee, Sarasota, Martin

### Custom / In-House
- **Pattern**: Varies — GET requests, HTML parsing, API reverse-engineering
- **Active Counties**: Orange, Seminole, Broward, St. Lucie, Indian River, Okeechobee, Alachua, Columbia, Clay, Bay, Okaloosa, Gadsden, Monroe, Leon, Dixie, Hernando, St. Johns

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
