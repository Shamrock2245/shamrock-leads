# Miami-Dade (FL/086) ArcGIS jail-bookings layer, 2026-10-08

Miami-Dade is the highest-volume FL county (Census Vintage 2024). It is in `config/write_counties.py` and writes live. Health says `unverified` and the matrix says `recon_only`.

## Source (checked from the box 2026-10-08 08:10 EDT)

| Item | Finding |
|---|---|
| Endpoint | `https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/ArcGIS/rest/services/miamidade_jail_data/FeatureServer/0/query`. It is anonymous and plain HTTPS, with no challenge. The MDCR inmate search has reCAPTCHA v2 and is not used. |
| Item | `c2275711ced240c6bc4e998ee1910e85` "Jail Bookings - May 29, 2015 to current" ("daily Miami-Dade Corrections jail bookings") |
| Fields | `BookDate` (date only, midnight ET), `Defendant`, `Address`, `CityStateZip`, `DOB`, `ChargeCode1`, `Charge1`, `ChargeCode2`, `Code2`, `ChargeCode3`, `Charge3`, `Zip`, `Filler`, `City`, `State`, `Zip1`, `ObjectId`, `GlobalID`. There is **no bond field**, **no custody status** and **no booking number**. |
| Size / freshness | 516,951 rows. The newest `BookDate` is 2026-10-07, so the data runs about one day behind. Oct 4 onward has 476 rows (about 160 per day). |
| Update method | `editingInfo.lastEditDate` = `schemaLastEditDate` = 2026-10-08 08:05 EDT, `hasStaticData: true`, and ObjectIds run contiguously 1..516,951. This looks like a full daily overwrite, which may reissue `GlobalID`s. |

## What changed

- **Bond:** it was `"0"` on every row, an invented $0. It is now `""` with `extra_data.bond_published = False`. `packet_builder_service.NO_BOND_ROSTER_COUNTIES` adds `("FL", "miami-dade")`, so already-written docs whose raw bond is `"0"` hydrate as unknown. A staff-entered amount still wins.
- **Charges:** `Charge1 | Code2 | Charge3`. It is empty when none are listed; the `UNKNOWN CHARGE` placeholder is gone.
- **Transport:** plain `requests`. The curl_cffi `impersonate="chrome131"` session is retired.
- **Fail loud:** a `returnCountOnly` count runs first. The scraper pages until it has fetched exactly that many rows and raises `MiamiDadeContractError` on an HTTP or ArcGIS error, a response without `features`, a feature missing any of `ObjectId, GlobalID, BookDate, Defendant, Charge1, Code2, Charge3`, a repeated ObjectId, a short walk, or a window above the 2,000-row cap. Before, any error was logged and the loop `break`, which returned a truncated batch as success.
- Unchanged: the 3-day window, the field list (no address, ZIP or DOB), the name parsing, the date-only booking date, and the `GlobalID`/`ObjectId` key.

## Read smoke (box, 2026-10-08 08:15 EDT)

360 rows, 360 unique keys. BookDate 2026-10-05: 132, 2026-10-06: 113, 2026-10-07: 115. 353 rows have charges, 7 have none. No bond values (all unknown). 3.7 s.

## Open item: booking key (why this stays `recon_only`)

The layer publishes no booking number, so the scraper keys on `GlobalID` (a GIS row id). It is source-issued, not invented. But if the county overwrites the layer daily (the signals above suggest it does), the same booking may get a new `GlobalID` each day, and each run would then insert duplicate leads for the 3-day window. This was not proven either way today. A non-PII snapshot (`ObjectId`, `GlobalID`, `BookDate` and a 16-hex row hash for 841 rows booked since Oct 1) is saved on the box at `/workspace/fl67/miami_dade_gid_snapshot_2026-10-08.json`. It is not committed. After the next layer edit, compare the same row hashes:

- If the `GlobalID`s match, the key is stable and the county can go to Leads Ops for a write smoke.
- If they differ, the key is not stable. Miami-Dade then needs an owner decision (fail_closed, or a different source with a real booking number). The MDCR search is reCAPTCHA-gated, so it is out of scope.

Health is not changed in this PR.
