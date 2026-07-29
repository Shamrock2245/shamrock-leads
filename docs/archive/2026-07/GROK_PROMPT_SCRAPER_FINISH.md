# Grok CLI Prompt — Finish Scraper Ecosystem Expansion

**Date:** 2026-07-20  
**Author:** Brendan (via Manus)  
**Usage:** Feed this file to Grok as context: `grok --file docs/GROK_PROMPT_SCRAPER_FINISH.md`

---

## Role

You are the Lead OSINT Architect and Senior Python Developer for the ShamrockLeads Bail Bonds CRM.

## Context

We are expanding our multi-state asynchronous scraper ecosystem (Python 3.11, FastAPI, Motor, Pydantic v2). We just pushed a major update to `main`:

1. **Rewrote the Zuercher base scraper** to use pure JSON APIs (`/api/portal/inmates/load`), saving massive RAM by dropping Chromium. Verified 7/8 SC portals working (1,754 inmates).
2. **Built CT (Statewide)** scraper — ASP.NET form bypass, 441 docket entries per run, pure HTTP.
3. **Built MS (Hinds County)** scraper — Classic ASP pagination, 700+ inmates, pure HTTP.
4. **Scaffolded stubs** for AL (Jefferson, Mobile, Madison) and MS (Jackson) — all returning 403 WAF/Cloudflare blocks from datacenter IPs.

A self-hosted residential proxy pool (Warren / S5W2C) is being deployed this morning.

## Your Mission

Execute the following tasks to finish the scraper ecosystem expansion:

### 1. BUILD THE APE (Autonomous Proxy Engine) ROUTER

Write or update `scrapers/proxy_engine.py` to implement a stealth request session using `curl_cffi` (impersonating a Chrome/JA3 fingerprint). Requirements:

- Route traffic through the local SOCKS5 residential proxy pool (Warren) at `socks5://127.0.0.1:1080` (configurable via `WARREN_PROXY_URL` env var).
- Implement exponential backoff with jitter on 403/429 responses.
- Atomic proxy rotation — if a proxy fails 3x consecutively, mark it dead and rotate.
- Session cookie persistence across paginated requests.
- Fallback chain: Warren residential → S5W2C mobile → stormsia free list → direct (datacenter).
- Thread-safe — must work with `SCRAPER_MAX_CONCURRENT = 8` threadpool.

### 2. UNBLOCK THE WAF-BLOCKED STUBS

Update the following stubs to use the new `curl_cffi` APE session:

| File | Portal | Platform |
|------|--------|----------|
| `scrapers/counties_al/jefferson.py` | `http://sheriff.jccal.org/NewWorld.InmateInquiry/AL0010000/` | New World (Tyler Tech) |
| `scrapers/counties_al/mobile.py` | `https://all.mobileso.com/OthReports/CurrentInmates.aspx` | Custom ASP.NET |
| `scrapers/counties_al/madison.py` | `https://www.madisoncountyal.gov/departments/sheriff/inmate-information` | CivicPlus |
| `scrapers/counties_ms/jackson.py` | `https://services.co.jackson.ms.us/inmatedocket/_inmateList.php?Function=list&Page=1&Order=BookDesc` | Custom PHP + Cloudflare |

Each must:
- Inherit from `BaseScraper` (see `scrapers/base_scraper.py`).
- Use the APE stealth session instead of raw `requests`.
- Parse into the 41-column `ArrestRecord` schema (see `core/models.py`).
- Dedup on `County` + `Booking_Number`.

### 3. SCAFFOLD THE REMAINING EXPANSION STATES

Generate scraper classes for targets that are currently empty or non-functional:

**Tennessee:**
- TnCIS (Statewide) — `lgc-tn.com/tncis-web-inquiry/` — Cloudflare protected, needs Obscura browser or APE.
- Shelby County (Memphis) — custom portal.
- Davidson County (Nashville) — custom portal.

**Texas:**
- Bexar (San Antonio) — verify if existing stub works or needs APE.
- Dallas — verify if existing stub works or needs APE.
- Harris (Houston) — verify if existing stub works or needs APE.

**Louisiana:**
- Orleans (New Orleans) — verify if existing stub works or needs APE.
- Lafayette — verify if existing stub works or needs APE.

Use the stealth proxy engine by default. If an endpoint is unknown, write the stealth request scaffold and leave a `# TODO: verify endpoint URL` comment.

### 4. UPDATE THE NLP PIPELINE

Update `osint-worker/legal_nlp_service.py` to parse raw charge strings from the new states:

- **CT format:** `"DISPOSITION DOCKET - Disposition"`, `"PRE-TRIAL DOCKET - PreTrial"`
- **MS format:** `"DUI: Third Offense"`, `"Cont. Subst.: Possession of Sc"`, `"Simple Assault - with weapon"`
- **AL format:** TBD (New World / ASP.NET — will be similar to FL/GA patterns)

Map these into the standardized risk engine categories used by `osint-worker/risk_engine.py`.

## Constraints

- **The Chain Is Law:** All data must map to the `ArrestRecord` 41-column schema. No bypassing lifecycle stages.
- **Idempotent Writes:** Dedup key is strictly `County` + `Booking_Number` (or `Docket_Number` for CT).
- **Fail Closed / Audit Everything:** Catch missing HTML selectors gracefully without crashing the execution loop. Log warnings, not exceptions.
- **Zero PII in standard error logs.** Never log defendant names, DOBs, or addresses at INFO level.
- **Secrets are Sacred:** Proxy credentials, API keys, and tokens stay in env vars or encrypted collections. Never hardcode.
- **Output production-ready, modular Python code.** Follow existing patterns in `base_scraper.py` and `zuercher_base.py`.

## Reference Files

Read these files for architectural context before writing code:

```
scrapers/base_scraper.py          — Base class all scrapers inherit from
scrapers/zuercher_base.py         — Example of pure-HTTP JSON API scraper (just rewritten)
scrapers/counties_ct/statewide_docket.py — Example of ASP.NET form bypass
scrapers/counties_ms/hinds.py     — Example of Classic ASP pagination scraper
core/models.py                    — ArrestRecord 41-column schema
config/settings.py                — All env var configs
docker-compose.yml                — Service architecture and resource limits
docs/VPS_ASSESSMENT.md            — Current resource analysis
```
