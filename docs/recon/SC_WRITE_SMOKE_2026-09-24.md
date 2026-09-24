# SC Write Smoke — 2026-09-24 (ET)

Branch: `fix/sc-ssw-write-aiken-darlington`  
Cite: `docs/recon/SC_READ_WRITE_HEALTH_2026-09-23.md`, `docs/recon/PALMETTO_READ_WRITE_HEALTH_2026-09-23.md`.

## Smoke table (Mongo write)

| County | Family | Scraped | New | Updated | Status | Source key |
|--------|--------|--------:|----:|--------:|--------|------------|
| Dorchester | Southern SW | 331 | 331 | 0 | ok / live_write | Citizen Connect `BookingID` |
| Chesterfield | Southern SW | 128 | 128 | 0 | ok / live_write | Citizen Connect `BookingID` |
| Aiken | DTNSearch | 392 | 392 | 0 | ok / live_write | Inmate ID# / `qSO_NO` |
| Darlington | DCN (HTTP) | 100 | 100 | 0 | ok / live_write | URL `bid` (page 1 of ~233) |

Health: `verified_public` for Dorchester, Chesterfield, Aiken, Darlington (plus existing Charleston).

## Still hold (honest)

| County | Reason | Brendan blocker |
|--------|--------|-----------------|
| Sumter | SmartCOP invents `LAST_YYYYMMDD` booking keys; `SOURCE_CONTRACT_VALIDATED=False` | Real SmartCOP source booking key on live source |
| Richland | JMSOnline maintenance; list would invent `RIC_` keys; fail_closed | JMSOnline out of maintenance **and** list exposes source booking # |
| Hampton | `hamptonjailroster.org` HTTP 403 / Cloudflare from ordinary egress | Ordinary public access (no WAF/residential bypass) |
| Marlboro | `marlborocountyjailsc.org` HTTP 403 / Cloudflare | Ordinary public access (no WAF/residential bypass) |

## Notes

- Dorchester Citizen Connect index returns HTTP 403; soft-continue with `agency_id` as JMS still returns cards with source BookingIDs.
- Darlington **HTTPS** connect-timeouts; **HTTP** `/dcn/inmates` serves DevExpress roster. First page ≈100 of 233; pagination AJAX not pursued this pass.
- Aiken TLS OK from Mac via curl_cffi; blank search requires last-name letter; A–Z walk yields 392 unique Inmate ID#s.
- No fail_closed reopen. No synthetic CHS_/RIC_/AIK_/DAR_/Sumter keys invented.
