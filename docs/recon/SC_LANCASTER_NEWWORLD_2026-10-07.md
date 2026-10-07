# SC Lancaster — NewWorld InmateInquiry (2026-10-07)

**Scope:** South Carolina broken-queue beyond Florence/Newberry — Lancaster only.
**Method:** plain HTTPS from the agent box (`requests`; no TLS impersonation, stealth browser, proxy, Obscura, or CAPTCHA/WAF bypass).
**Privacy:** no personal data committed. Booking-key format shown as pattern only.
**Write smoke:** `MONGODB_URI` was not available here. Read/recon only. Do **not** promote to `verified_public` until a Mac/VPS write smoke lands.

## Contract

| Field | Finding |
|---|---|
| Official source | https://inmate.lancastercountysc.net/NewWorld.InmateInquiry/SC0290000 |
| Access | HTTP 200, ordinary public HTML |
| Broad roster | `InCustody=True` listing; pagination `Page=2..N` (observed 100 + 100 + 14 ≈ 214 links) |
| Source booking ID | Detail page `<label>Booking</label><span>YYYY-########</span>` |
| Not a booking key | `/Inmate/Detail/{id}` path id (negative integer) — listing amenity only |
| Charges | Detail `BookingCharges` grid (`Charge Description`) |
| Bond | Detail `Total Bond Amount` (`$…`). Per-charge `Bond` cells may hold bond *reference* numbers (`YYYY-########`) — never treat those as dollars |
| Holds untouched | Richland, Sumter, Hampton, Marlboro, Oconee, Pickens, and other SC fail_closed |

## Outcome

- **Fixed** thin NewWorld wrapper → dedicated `scrapers/counties_sc/lancaster.py` with `SOURCE_CONTRACT_VALIDATED=True`.
- Health / `SCRAPER_SOURCE_STATES` left **unverified** (default) until write smoke.
- Evidence row SC FIPS `057` → `productive` / matrix `candidate_productive`.

## Next

1. Mac or VPS **write smoke** (`python main.py` Lancaster / SC Lancaster) → then consider `verified_public` + `live_emitter_evidence.json`.
2. Continue SC broken queue (next candidates after Lancaster; keep Zuercher Oconee/Pickens held unless independently proven).
