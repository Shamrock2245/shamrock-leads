# Texas County Scraper Registry

> **Last Updated:** 2026-07-22  
> **Registered (dashboard):** 4 wave-1 scrapers (Bexar, Dallas, Harris, Tarrant)  
> **Package:** `scrapers/counties_tx/`  
> **Job IDs:** `scraper_tx_<county>` · CLI: `.venv/bin/python main.py tx_tarrant`

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

## Next Targets (Top Population Priority)

| County | Population | Priority | Target Portal / Strategy | Notes |
|--------|-----------:|----------|-------------------------|-------|
| **Travis** | ~1.3M | High | https://public.co.travis.tx.us/sips/ | SIPS portal with `verify=False` HTTP / DrissionPage fallback |
| **Collin** | ~1.1M | High | https://cijspub.collincountytx.gov/ | DFW metro; Odyssey/Tyler public portal |
| **Denton** | ~1.0M | Medium | https://inmatesearch.dentoncounty.gov/ | DFW metro; jTable / ASP.NET inmate lookup |
| **Hidalgo** | ~880k | Medium | Rio Grande Valley Sheriff | South TX high-volume border county |
| **El Paso** | ~860k | Medium | https://www.epcounty.com/sheriff/ | West TX regional hub |
| **Fort Bend** | ~860k | Medium | Fort Bend Sheriff Portal | Houston metro expansion |

---

## Notes & Gotchas

- **Dallas County AR vs TX**: Do not use `myr2m.com/DallasCoRoster` — that roster is Dallas County, Arkansas (~100 inmates), not Dallas County, TX.
- **Bexar Magistrate**: CSV jail activity reports (`edocs.bexar.org`) exist, but central magistrate HTML via `make_stealth_request` is the primary high-yield source.
- **Tarrant Dual Endpoint**: Magistration docket (`/Home/GetDocketResults`) provides exact offense titles, while inmate search (`/Home/GetSearchResults`) enriches demographics.
- **curl_cffi TLS Signatures**: Installed `curl_cffi` 0.15.0 supports `chrome124`, `chrome120`, `chrome110`, `chrome`, `safari15_5`. `TLSFingerprinter` is configured to rotate across these supported signatures.
