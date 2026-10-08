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

## Detail pages: custody status and per-case bonds (2026-10-08 ~09:30 EDT)
`JailSearchDetails.aspx?BookNo=<booking #>` (the roster's first-cell link) answers plain HTTPS GET 200. It has:
- `fvBook`: Booking # and `lblReleaseDateTime`. `-` means in custody; `MM/DD/YYYY HH:MM` means released.
- One `Case Seq.` table per case (Court Case #, Agency Case #). Each holds a `Statute | Statute Description | Counts | Bond Amount | Other Information` grid.

Live survey (7-day window, 82 bookings, counts only):
- 82 of 82 detail GETs returned 200.
- 53 were in custody (`-`) and 29 released, so the old code mislabeled 29 released people as In Custody.
- Charge bond cells: 93 positive `$N`, 59 `$0.00`.
- Bookings whose cells are all `$0.00`: 29 in custody and 1 released. Mixed: 10 in custody and 1 released. All positive: 13 in custody and 27 released.
- `$0.00` statutes are mostly 948.06 (VOP), 00.00 (holds/warrants) and 784.03. Other Information said `ROR` on 1 `$0.00` row.

So **`$0.00` is the "no bond set" placeholder, not a published $0**. It is unknown unless the row says ROR. Hernando stays in `NO_BOND_ROSTER_COUNTIES`; a detail-derived amount sets `extra.bond_published` true.

Scraper live run with the fix: 82 records, all keyed on source booking numbers. 53 In Custody and 29 Released (all 29 with a Release_Date). 81 have charges and 54 a court case number. 41 have a complete positive bond and 41 are unknown; none is `"0"`.
