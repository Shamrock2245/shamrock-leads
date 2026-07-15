# Texas County Scraper Registry

> **Last Updated:** 2026-07-15  
> **Registered (dashboard):** 3 wave-1 scrapers  
> **Package:** `scrapers/counties_tx/`  
> **Job IDs:** `scraper_tx_<county>` · CLI: `python main.py tx_bexar`

## Wave-1 (registered)

| County | Scraper | Portal | Status | Notes |
|--------|---------|--------|--------|-------|
| **Bexar** | `bexar.py` | https://centralmagistrate.bexar.org/ | ✅ Live | 24h Class B+ magistrate list (~180–250/day) |
| **Dallas** | `dallas.py` | https://www.dallascounty.org/jaillookup/ | ✅ Live | Name+race+sex grid; hour-rotated letter blocks |
| **Harris** | `harris.py` | HCSO Find Someone in Jail | ⏳ Browser | DrissionPage A–Z last-name walk |

## Next targets (top population)

| County | Portal notes | Priority |
|--------|--------------|----------|
| Tarrant | Jail search TLS/DNS unstable from recon host | High |
| Travis | SIPS `public.co.travis.tx.us` SSL picky | High |
| Collin | TBD | Medium |
| Denton | TBD | Medium |
| Hidalgo | TBD | Medium |
| El Paso | TBD | Medium |
| Fort Bend | TBD | Medium |

## Notes

- **Do not** use `myr2m.com/DallasCoRoster` — that roster is Dallas County **AR** (~100 inmates), not TX.
- Odyssey/Tyler cloud counties need WAF strategy (`_get_obscura_browser` / residential proxy).
- Bexar CSV jail activity reports (`edocs.bexar.org`) exist but SSL failed from recon host; magistrate HTML is preferred.

## Smoke results (2026-07-15)

| County | Records (one-shot) |
|--------|-------------------:|
| Bexar | ~189 |
| Dallas | ~141 (30-request smoke; full grid higher) |
| Harris | browser-dependent |
