# SC Florence + Newberry field completeness — 2026-10-07

> Ranked PR #4. Field fix only for already-productive `live_write` scrapers.
> Fail_closed SC counties (Richland, Sumter, Hampton, Marlboro, …) untouched.

## Prior live_write evidence (unchanged facts)

| County | Evidence | Cite |
|---|---|---|
| Florence | 624 Mongo arrests; status ok/623 same day (2026-09-23) | `docs/recon/SC_READ_WRITE_HEALTH_2026-09-23.md` |
| Newberry | 19 Mongo arrests morning write; PDF flaky later (2026-09-23) | same |

Both were Health=`unverified` by default despite productive writes.

## Field gaps closed (source-published only)

### Florence — `booking.fcso.org`

- Listing letter-walk still yields name / age / race / sex / admit date + `inmate-details` link.
- Detail Charge grid (ordinary GET) publishes **Charge**, **Bond ($)** , **Bond Type**, **Arresting Agency**, and **Name ID**.
- `Booking_Number` = source **Name ID** only. Synthetic `FLO_` keys removed.
- `Charges` / `Bond_Amount` / `Bond_Type` come from the detail grid. Empty/$ absent → `Bond_Amount="0"` (never invented).
- Rows without a parseable Name ID are skipped.

### Newberry — Sheriff current-bookings PDF

- PDF layout (pypdf): `LAST, FIRST - XX-NNNNNNN - AGE` then charge lines.
- Source keys accepted: `SO` / `NP` / `HP` / `PP` / `HA` / `SL` / `GS` (all printed on the official PDF). Legacy `SO#` fixture form retained.
- `Charges` joined from PDF charge lines (deduped). No more hardcoded `"Unknown"`.
- `Bond_Amount` only when a `$` amount is printed. Release labels (`BOND POSTED`, `PR BOND`) are **not** dollar bonds.

## Health promotion

`SCRAPER_SOURCE_STATES["Florence (SC)"]` and `["Newberry (SC)"]` → `verified_public`, with matching `live_emitter_evidence.json` rows citing the 2026-09-23 write health plus this field probe. Matrix regenerated via `scripts/build_recon_matrix.py`.

## Out of scope / follow-ups

- Florence DevExpress pager beyond page 1 of each letter (pageCount observed >1 for letter A).
- Newberry afternoon empty runs (PDF link churn) — telemetry only.
- No reopen of Richland / Sumter / Hampton / Marlboro / other fail_closed SC counties.
