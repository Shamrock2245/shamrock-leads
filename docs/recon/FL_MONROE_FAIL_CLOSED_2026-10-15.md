# Monroe (FL): fail closed until the offense-number key is proven (2026-10-15)

**Decision (CoS, 2026-10-15):** Monroe is `fail_closed`, with no source request and no writes, until the offense-number key is proven to be one-per-booking.

## Source
- `GET https://data.keysso.net/api/arrests`: the Keys SO arrest log.
- 2026-10-15 at about 9:10 AM ET from the agent box: one plain HTTPS GET with an honest UA and a verified certificate returned HTTP 200 JSON with 63 rows and no challenge. The certificate chain verifies.

## Why closed
- The feed has no source booking number.
- The old parser keyed rows on the MNI in the mugshot filename (`MCSO<YY>MNI<NNNNNN>`). That is a person-level Master Name Index id, the same problem that closed Hendry (2026-10-08) and Alachua.
- When there was no MNI, it fell back to a synthesized `MONROE-{OffenseNo|CADno}`. The offense number has not been shown to be unique per booking (one arrest could carry several, or a person could repeat), so it is not a proven booking key.
- It is an arrest log, so there is no custody field.

## What is in place
- `scrapers/counties/monroe.py` has `SOURCE_CONTRACT_VALIDATED = False`. `scrape()` returns `[]` before any request.
- The honest-HTTPS parser is kept in `_fetch_and_parse()`: unknown bond is `""`, a published 0 is kept, and Status is `""`.
- Health `SCRAPER_SOURCE_STATES["Monroe (FL)"] = "fail_closed"`. `config/source_guard.SOURCE_HOST_LABELS` maps `keysso.net` to `Monroe (FL)`, so rechecks and detail fetches are refused too.
- Rows already stored are untouched.

## To reopen
Prove from the source, across several log snapshots, that `OffenseNo` (or another source field) is issued once per booking and is stable. Get CoS sign-off, switch the key to it (and never the MNI), then set `SOURCE_CONTRACT_VALIDATED = True` and the Health state.
