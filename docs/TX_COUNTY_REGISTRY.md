# Texas County Scraper Registry

> **Last Updated:** 2026-07-24  
> **Registered (dashboard):** 12 scrapers — wave-1 (Bexar, Dallas, Harris, Tarrant) + wave-2 (Travis, Collin, Denton) + wave-3 (Fort Bend, Montgomery, Williamson, El Paso, Hidalgo)  
> **Package:** `scrapers/counties_tx/`  
> **Job IDs:** `scraper_tx_<county>` · CLI: `.venv/bin/python main.py tx_fort_bend`

## Stealth Stack Integration

All Texas scrapers use the 4-layer stealth stack:
1. **IP Egress Layer**: Autonomous Proxy Engine (`get_ape()`) for proxy rotation & residential egress when targeting WAF/Cloudflare.
2. **TLS / HTTP Egress Layer**: `make_stealth_request` with `curl_cffi` impersonation (`chrome124`, `chrome120`, `safari15_5`).
3. **Engine Layer**: DrissionPage Chromium with `--disable-blink-features=AutomationControlled` + `_inject_stealth_js(page)` (patches `navigator.webdriver`, `plugins`, `languages`, `window.chrome.runtime`).
4. **Behavioral Simulation Layer**: `BehaviorSimulator` random non-uniform pauses and realistic Chrome 126 headers.

---

## Wave-1 (Registered & Operational)

| County | Population | Scraper | Portal / Strategy | Status | Scraped (one-shot) | Notes |
|--------|-----------:|---------|------------------|--------|------------------:|-------|
| **Harris** | ~4.7M | `harris.py` | HCSO Find Someone in Jail | ✅ Live | Browser-rendered | DrissionPage A–Z last-name walk + `_inject_stealth_js()` |
| **Dallas** | ~2.6M | `dallas.py` | https://www.dallascounty.org/jaillookup/ | ✅ Live | ~140–500+ | Hour-rotated letter block grid via `make_stealth_request` |
| **Tarrant** | ~2.1M | `tarrant.py` | https://inmatesearch.tarrantcounty.com/ | ✅ Live | ~800 | Dual-endpoint: `/Home/GetDocketResults` (offenses) + `/Home/GetSearchResults` |
| **Bexar** | ~2.0M | `bexar.py` | https://centralmagistrate.bexar.org/ | ✅ Live | ~254 | 24h Central Magistrate list via `make_stealth_request` |

---

## Wave-2 (Registered & Operational)

| County | Population | Scraper | Portal / Strategy | Status | Notes |
|--------|-----------:|---------|------------------|--------|-------|
| **Travis** | ~1.3M | `travis.py` | https://public.traviscountytx.gov/sip/api/v2/inmates | ✅ Live | SIPS REST API; A-Z lastName walk + detail fetch per bookingNumber; charges, bond, facility, agency |
| **Collin** | ~1.1M | `collin.py` | https://apps.collincountytx.gov/JailInmates/ | ⚠️ WAF-gated | Incapsula WAF; requires APE residential proxy. Fallback: Odyssey on `cijspub.co.collin.tx.us` |
| **Denton** | ~1.0M | `denton.py` | https://athena.dentonpolice.com/JailView/ | ✅ Live | Athena JailView WebMethod (`GetInmates`); city jail only; JSON API |

---

## Wave-3 (Registered & Operational)

| County | Population | Scraper | Portal / Strategy | Status | Notes |
|--------|-----------:|---------|------------------|--------|-------|
| **Hidalgo** | ~880k | `hidalgo.py` | https://www.hidalgocounty.us/sheriff | ✅ Live | Rio Grande Valley border county; A-Z walk via `make_stealth_request` |
| **El Paso** | ~860k | `el_paso.py` | https://www.epcounty.com/sheriff/ | ✅ Live | West TX hub; A-Z walk via `make_stealth_request` |
| **Fort Bend** | ~860k | `fort_bend.py` | https://pos.fortbendcountytx.gov/ | ✅ Live | Houston Metro expansion; A-Z JSON API walk |
| **Montgomery** | ~650k | `montgomery.py` | https://mctxsheriff.org/inmate_inquiry/ | ✅ Live | North Houston metro; A-Z walk via `make_stealth_request` |
| **Williamson** | ~640k | `williamson.py` | https://www.wilco.org/Sheriff/Inmate-Search | ✅ Live | Austin Metro expansion; JailView A-Z JSON API |

---

## Notes & Gotchas

- **Dallas County AR vs TX**: Do not use `myr2m.com/DallasCoRoster` — that roster is Dallas County, Arkansas (~100 inmates), not Dallas County, TX.
- **Bexar Magistrate**: CSV jail activity reports (`edocs.bexar.org`) exist, but central magistrate HTML via `make_stealth_request` is the primary high-yield source.
- **Tarrant Dual Endpoint**: Magistration docket (`/Home/GetDocketResults`) provides exact offense titles, while inmate search (`/Home/GetSearchResults`) enriches demographics.
- **curl_cffi TLS Signatures**: Installed `curl_cffi` 0.15.0 supports `chrome124`, `chrome120`, `chrome110`, `chrome`, `safari15_5`. `TLSFingerprinter` is configured to rotate across these supported signatures.
- **Travis SIPS API**: Direct JSON REST API at `public.traviscountytx.gov/sip/api/v2`. No WAF. List endpoint returns `[{id, bookingNumber, fullName, age}]`; detail returns charges array with `chargeText`, `bondAmount`, `bondType`, `chargeLevel`, `court`.
- **Collin Incapsula**: Both `www.collincountytx.gov` and `apps.collincountytx.gov` are behind Imperva/Incapsula WAF. Requires APE residential proxy rotation to bypass. Odyssey PublicAccess on `cijspub.co.collin.tx.us` returns 503 (maintenance) as of 2026-07-22.
- **Denton Athena**: City Police JailView at `athena.dentonpolice.com` serves a WebMethod (`GetInmates`) returning all current inmates as a JSON string in `{d: "[...]"}`. County-level Tyler/Odyssey at `justice1.dentoncounty.gov` is misconfigured (returns Public Access Error).
