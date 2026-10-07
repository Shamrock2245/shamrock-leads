# SC Oconee + Pickens — Zuercher public roster, no booking ID (2026-10-07)

**Scope:** South Carolina Zuercher thin wrappers still inheriting `SOURCE_CONTRACT_VALIDATED=True` while recon showed no source booking/inmate ID.
**Method:** plain HTTPS recon of the public Zuercher portals (no TLS impersonation, stealth browser, proxy, Obscura, or CAPTCHA/WAF bypass).
**Privacy:** no personal data committed.

## Summary

| County | Official source | Broad public roster | Source booking / inmate ID | Outcome |
|---|---|---|---|---|
| **Oconee** | https://oconee-so-sc.zuercherportal.com/ | Yes — public inmates module | **None** on listing or detail | **fail_closed** — do not invent keys |
| **Pickens** | https://pickens-so-sc.zuercherportal.com/ | Yes — public inmates module | **None** on listing or detail | **fail_closed** — do not invent keys |

## Why hold

Both portals serve a public custody roster, but rows do not expose a stable source-issued booking number or inmate ID. Emitting without that key would force invented identifiers and break `State + County + Booking_Number` uniqueness. Same posture as Colleton / Kershaw Zuercher holds.

## Code / Health

- `scrapers/counties_sc/oconee.py` and `pickens.py`: `SOURCE_CONTRACT_VALIDATED=False` + `SOURCE_SAFETY_REASON` (match Colleton/Kershaw pattern).
- `SCRAPER_SOURCE_STATES`: `Oconee (SC)` / `Pickens (SC)` → `fail_closed`.
- Evidence FIPS `073` / `077` → `fail_closed`; matrix regenerated.

## Next

Stay fail_closed until the SO publishes a source booking or inmate ID on the ordinary public roster (or an ordinary public detail field). No invented keys.
