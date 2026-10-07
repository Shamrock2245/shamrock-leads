# York (SC) — Inmates in Jail roster, source Booking Number (2026-10-07)

**Scope:** York was held `fail_closed` because the configured roster timed out through ordinary access. This recon re-tested it.
**Method:** plain HTTPS from the agent box with Python `requests` and an honest UA. No proxy, TLS impersonation, stealth browser, Obscura, or CAPTCHA/WAF bypass. Only the public listing pages were read. No detail or person-level endpoints exist or were probed.
**Privacy:** no personal data recorded. Formats are shown as patterns only.
**Write smoke:** `MONGODB_URI` is not available on the box. Health stays **unverified** until a Mac/VPS write smoke.

## Source

| Item | Finding |
|---|---|
| Official URL | https://inmatesinjail.yorkcountygov.com/detentioncenter/inmatesinjail.aspx (York County government host) |
| Access | 200 to a plain GET. The bare host root is 403, but the roster path is public |
| Listing | ASP.NET DataGrid `dgJackets`, 15 people per page, pager `__doPostBack('dgJackets$ctl01$ctlNN')` with `...` to the next block |
| Walk | 29 pages, 435 rows, matching `Results Count: 435` |
| Source booking key | `Booking Number` → `DC<YYYY><NNNNN>`. 435 of 435 were unique. The photo path `/photos/<Booking Number>.jpg` matched on every row |
| Booking date/time | `Booking Date` `M/D/YYYY h:mm:ss AM/PM` on every row |
| Status | `Release Date` = `*In Jail` on all 435 rows ("Only current booking information is available") |
| Bond | `Total Bond` dollars, positive on 294 rows. `$0.00` is stored as `0` |
| Charges | Nested grid: Sequence# / Charge Description / Arresting Agency, present on 429 rows |
| Person fields | Name (`Last , First Middle`), City, State/Zip, Race/Sex, Height/Weight, Age |

## Parser contract (`scrapers/counties_sc/york.py`)

- `Booking_Number` is the published Booking Number only, and must match `^DC\d{9}$`. A row is dropped if its single photo key disagrees with it. Nothing is synthesized.
- A page with people rows but no keyed booking raises `ParseDriftError`. A missing grid also raises `ParseDriftError`. A 403 raises `AntiBotBlocked`.
- The scraper walks every page, capped at 80, with 0.4 s pacing. It logs a warning if the walked row count differs from `Results Count`.
- Old bugs fixed: the name was read from the bookings table's facility header (`York County Detention Center`), only page 1 was parsed, and `Total Bond` was ignored.

## Next

1. Mac or VPS write smoke: `python main.py york`. Then set `SCRAPER_SOURCE_STATES["York (SC)"] = "verified_public"` and add a `live_emitter_evidence.json` row.
