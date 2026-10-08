# Charlotte FL Revize roster hardening — 2026-10-07

Owner priority follow-up to #113 (Sarasota/Manatee). Same Revize CMS
`/bookings` design as Manatee; Charlotte previously kept Bond_Amount `"0"`,
read six positional cells (no Released), dropped every charge after the
first, and ended the walk silently on a Cloudflare page.

## Live evidence (box egress)

| URL | Result (7:12 PM ET) |
|---|---|
| `https://inmates.charlottecountyfl.revize.com/` | 403, `cf-mitigated: challenge`, `server: cloudflare`, title `Just a moment...` |
| `…/bookings` | same |
| `…/bookings?page=2` | same |

A residential exit is required. The live roster columns themselves were
**not** re-verified from the box; the contract below matches Manatee's
documented Revize headers and the pre-audit Charlotte docstring
(`Booking #, Last Name, First Name, Mid., Charge, Arrest Date`), plus the
`Released` column Manatee already required. The first Leads Ops residential
read smoke is the confirmation.

## Audit items

| # | Item | State | Evidence |
|---|---|---|---|
| 1 | Source contract | **blocked** from the box / **fixed** in code | CF 403 on every checked URL. Fail-closed guards now enforce the contract; Leads Ops must confirm headers from residential egress. |
| 2 | Booking keys | **fixed** | Columns mapped by header. Key is the source `Booking #`, checked against the row's `/bookings/<id>` link. A mismatch, any row with a blank / unrecognised booking number or too few cells, or the same booking on two people raises `ParseDriftError` (rows are never silently dropped). |
| 3 | Bond | **fixed** | `Bond_Amount=""` and `Bond_Type=""` (was `"0"`, which cost each lead −50). The roster publishes no bond. A source-published `$0` would still be kept as `"0"`. |
| 4 | Charges | **fixed** / **blocked** | Fixed: every charge row for a booking is kept (`Charges` pipe-joined + `extra.charge_details`). Before, only the first charge survived. Blocked: statute and degree are not on the roster; detail pages are CF-blocked. |
| 5 | Dates / Released / mugshot | **fixed** | Arrest Date is the booking date origin. `Released` is required; blank / In Custody / No / N/A → In Custody; Yes / Released / a date → Released; anything else fails closed. Mugshot only from a row `<img>`. |
| 6 | Address / DOB | **blocked** | Not on the roster; detail pages CF-blocked. |
| 7 | Paging guards | **fixed** | Fails closed on missing table or column, empty first page, empty or repeated page, `MAX_PAGES` with a next page still offered, or a walked count that differs from a published total. Before, a Cloudflare page or `new_count == 0` silently ended the walk. |
| 8 | Hydrate | **fixed** | Unknown bond stays blank (never `$0`). Legacy Charlotte docs that carry a scraped `"0"` are treated as unknown. Staff-positive amounts still win; a staff flag left behind a rescrape that rewrote the amount to `0.0` no longer hydrates as a known `$0`. |
| 9 | Health / matrix | **ok** | Health stays **`unverified`**. Evidence row FL/015 and the matrix are updated. Shared contract lives in `scrapers/revize_roster.py`. |

## Residential coverage

- Cloudflare challenge/block, or no verified US residential exit, raises the
  shared `EgressBlocked` (`anti_bot` + `egress_block=True`, never retried)
  and writes nothing.
- `CHARLOTTE_EGRESS_MODE=direct` for Leads Ops residential runs: no proxy, and
  the host must look US residential (unknown exit = not residential, per #113).
- Update (later 2026-10-07): the `auto` APE/Warren + office SOCKS path and the
  Patchright stealth launcher were removed. `direct` is the default and the
  only accepted mode. Charlotte is relay-only, and the VPS scheduler no longer
  runs it (`docs/ops/REVIZE_RELAY_RUN.md`).
- Read smoke (no writes, aggregates only):
  `CHARLOTTE_EGRESS_MODE=direct python scripts/charlotte_residential_smoke.py`
- Write smoke (needs `MONGODB_URI`):
  `CHARLOTTE_EGRESS_MODE=direct python main.py Charlotte`

## Remaining (owners)

| Item | Owner |
|---|---|
| Confirm live headers, Released values and pager text | Leads Ops residential read smoke |
| Prod write smoke; Health stays unverified until then | Leads Ops |
| Bond / statute / degree / DOB / address on `/bookings/<id>` | Follow-up once residential egress can reach detail pages |
| Whether `auto` still keeps the APE/office SOCKS path | CoS / Brendan (policy); Manatee cleanup is a separate PR |
