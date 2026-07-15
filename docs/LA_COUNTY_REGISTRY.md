# Louisiana Parish Scraper Registry

> **Last Updated:** 2026-07-15  
> **Registered (dashboard):** 2 wave-1 scrapers  
> **Package:** `scrapers/counties_la/`  
> **Job IDs:** `scraper_la_<parish>` · CLI: `python main.py la_orleans`  
> **Note:** LA uses *parish* not county — we store parish name in `County` field.

## Wave-1 (registered)

| Parish | Scraper | Portal | Status | Notes |
|--------|---------|--------|--------|-------|
| **Orleans** | `orleans.py` | https://www.opso.gov | ⏳ Partial | Beacon/OPSO paths; browser fallback; low yield |
| **Lafayette** | `lafayette.py` | 365Labs Community Portal | ⏳ Captcha | CAPTCHA gate on inmate list; API probe + browser |

## Next targets

| Parish | Platform | Priority |
|--------|----------|----------|
| East Baton Rouge | Custom / VINE | High |
| Jefferson | Custom | High |
| Caddo | TBD | Medium |
| Calcasieu | TBD | Medium |
| St. Tammany | TBD | Medium |
| LAVINE statewide | Appriss VINE | Medium (strict bot detection) |

## Lafayette 365Labs

- Agency UUID: `689d71d5-1d4e-4726-9cfb-a3c94dfb231e`
- Paths: `/InmateList/GetCaptcha`, `/InmateList/VerifyCaptcha`
- Production path likely needs audio captcha solver or residential session

## Smoke results (2026-07-15)

| Parish | Records (one-shot) |
|--------|-------------------:|
| Orleans | ~5 |
| Lafayette | 0 (captcha) |
