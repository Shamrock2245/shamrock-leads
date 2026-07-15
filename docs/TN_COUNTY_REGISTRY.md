# Tennessee County Scraper Registry

> **Last Updated:** 2026-07-15  
> **Registered (dashboard):** 3 wave-1 scrapers  
> **Package:** `scrapers/counties_tn/`  
> **Job IDs:** `scraper_tn_<county>` · CLI: `python main.py tn_davidson`

## Wave-1 (registered)

| County | Scraper | Portal | Status | Notes |
|--------|---------|--------|--------|-------|
| **Davidson** | `davidson.py` | https://dcso.nashville.gov | ✅ Live | RecentBookings + letter walk + detail bond/charges (~2.8k active) |
| **Knox** | `knox.py` | https://sheriff.knoxcountytn.gov/inmate.php | ✅ Live | Letter index; may serve maintenance placeholder |
| **Shelby** | `shelby.py` | https://imljail.shelbycountytn.gov/IML | ⏳ Hardened stub | TLS handshake issues from some stacks; curl_cffi preferred |

## Next targets

| County | Population rank | Likely platform | Priority |
|--------|----------------:|-----------------|----------|
| Hamilton (Chattanooga) | 4 | Custom / VINE | High |
| Rutherford (Murfreesboro) | 5 | Custom | High |
| Williamson | 6 | TBD | Medium |
| Montgomery | 7 | TBD | Medium |
| TnCIS rural cluster | 80+ | LGC Cloudflare | Medium (shared base) |

## Identity

- `ArrestRecord.State = "TN"`
- `scraper_id = scraper_tn_<county>`
- Never collapse with Davidson (NC) or Shelby (AL) same-name counties

## Smoke results (2026-07-15)

| County | Records (one-shot) |
|--------|-------------------:|
| Davidson | ~2822 |
| Knox | ~46 |
| Shelby | 0 (portal TLS) |
