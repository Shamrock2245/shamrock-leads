# Hernando (FL/053) unknown bond, 2026-10-08

## Live check (box, plain `requests`, 08:52–08:58 EDT)

- `https://www.hernandosheriff.org/jail/Applications/JailSearch/` answers plain HTTPS (no challenge). The booking-date POST (ViewState) returns the results grid: name, race/sex, DOB, a source booking number `HCSO<YY>JBN<NNNNNN>`, booking date/time and offenses. **The grid has no bond column.**
- Each row links to `JailSearchDetails.aspx?BookNo=…`, which does have per-case `Bond Amount` / `Bond Conditions` tables. That detail is **not** used yet; it is a possible follow-up for real bond values.
- 7-day read with this PR: 82 rows, 82 unique source booking numbers, 81 with offenses, booking date on all, 0 bonds (all unknown).

## Change

- `Bond_Amount` was hard-coded to `"0"` on every row. It is now `""` with `extra_data.bond_published = False`. `NO_BOND_ROSTER_COUNTIES` adds `("FL", "hernando")`: every old Hernando `"0"` was hard-coded, so it hydrates as unknown (a staff amount still wins).
- Rows without a source booking number are skipped. Before, the key fell back to the **name** (`key = booking_num or full_name`) and the record was emitted with an empty `Booking_Number`.
- No results table now raises `HernandoContractError` (a 7-day window always has bookings), and so does a table with no keyed rows. Before, it returned `[]` as success.
- Transport: plain `requests`; the curl_cffi `impersonate` session is retired.

## Not changed (follow-up)

The search posts `cbShowReleased=on`, so released bookings are included, yet every row is written `Status="In Custody"`. The detail page has `Release Date/Time`. Fixing status needs the detail fetch, so it is left for a follow-up PR and flagged.
