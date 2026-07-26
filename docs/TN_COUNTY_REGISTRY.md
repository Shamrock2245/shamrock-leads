# Tennessee County Scraper Registry

> **Last Updated:** 2026-07-26  
> **Registered (dashboard):** 9 scrapers (wave-1–3 + TnCIS)  
> **Package:** `scrapers/counties_tn/`  
> **Job IDs:** `scraper_tn_<county>` · CLI: `python main.py tn_davidson`

## Wave-1 (registered)

| County | Scraper | Portal | Status | Notes |
|--------|---------|--------|--------|-------|
| **Davidson** | `davidson.py` | https://dcso.nashville.gov | ✅ Live | RecentBookings + letter walk + detail bond/charges (~2.8k active) |
| **Knox** | `knox.py` | https://sheriff.knoxcountytn.gov/inmate.php | ✅ Live | Letter index; may serve maintenance placeholder |
| **Shelby** | `shelby.py` | https://imljail.shelbycountytn.gov/IML | ⏳ Hardened stub | TLS handshake issues from some stacks; curl_cffi preferred |

## Wave-2 / Wave-3 (registered)

| County | Scraper | Portal | Status | Notes |
|--------|---------|--------|--------|-------|
| **Hamilton** | `hamilton.py` | Chattanooga | ✅ Live | Wave-2 |
| **Rutherford** | `rutherford.py` | Murfreesboro | ✅ Live | Wave-2 |
| **TnCIS** | `tncis.py` | Statewide rural | ✅ Live | Shared LGC cluster |
| **Montgomery** | `montgomery.py` | Clarksville MCSO JSON | ✅ Live | ~600 current inmates |
| **Sumner** | `sumner.py` | MyOCV SumnerInmates.json | ✅ Live | ~702 · charges+bond from S3 |
| **Williamson** | `williamson.py` | JailTracker | ⚠️ CAPTCHA | Browser/JT base on VPS |

## Next targets

| County | Population rank | Likely platform | Priority |
|--------|----------------:|-----------------|----------|
| TnCIS rural expand | 80+ | LGC Cloudflare | Medium |

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
