# Hendry (FL): fail closed (2026-10-08)

**Decision:** CoS approved on 2026-10-08. Hendry goes to `fail_closed` in Health (`SCRAPER_SOURCE_STATES`), in code (`SOURCE_CONTRACT_VALIDATED=False`, no fetch), in the matrix, and as a `hold` row in `live_emitter_evidence.json`.

## Source check
Run from the agent box on 2026-10-08 at about 8:00 AM ET, with a plain HTTPS GET, no proxy and no impersonation. No personal data is recorded here.

- URL: `https://myocv.s3.amazonaws.com/ocvapps/a102933935/inmates.json` (MyOCV CMS feed behind the sheriff's app). HTTP 200 JSON, 288 rows.
- The only identifier on each row is `inmateID`, shaped `HCSO<YY>MNI<NNNNNN>`. MNI is a Master Name Index, which is a **person** id. Its two-digit year runs from 00 to 26 whatever the row's Booked Date year is (for example, MNI year 00 on 2026 bookings). So it identifies the person, not the booking.
- `chargeArray` is schema-only: field names, with no charge rows. No bond is published.
- Booked date and time are present, but without a booking number a re-booking of the same person collides with their old row.

## Rule
Person ids are not booking keys. Alachua (#107, MNI-only grid), Clay (FL, name and date only) and Durham (NC) are already fail closed on the same rule. Hendry stays closed until the source publishes a booking number.

## What changes
- `scrapers/counties/hendry.py` becomes a fail-closed stub (`SOURCE_CONTRACT_VALIDATED=False`, `scrape()` returns `[]`, no network).
- `dashboard/extensions.py`: `"Hendry (FL)": "fail_closed"`.
- `docs/recon/county_source_contract_evidence.json` (FL/051) is set to `fail_closed`, and `live_emitter_evidence.json` gets a `hold` row.
- The matrix is regenerated, and the `FL_67_STATUS` and `COUNTY_REGISTRY` rows are updated.

## What does not change
- **Prod data:** nothing is written or deleted. Rows already stored under MNI keys stay until Brendan OKs a cleanup.
- **Lead views:** with #133 (fail_closed out of the default lead list), Hendry rows stop showing in the default list, the SWFL/Write Book presets and the bond-ready queue. Picking Hendry by name, or passing `include_fail_closed=true`, still shows them. Hendry is one of the `KEY_FL_COUNTIES` home counties, so this affects day-to-day views.
