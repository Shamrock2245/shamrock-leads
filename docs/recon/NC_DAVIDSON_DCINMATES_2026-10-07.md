# Davidson County (NC) — DCInmates dhtmlxGrid + details ashx

**Date:** 2026-10-07  
**Scope:** Lift the fail_closed gate on Davidson after proving an ordinary public bulk roster with source-issued booking numbers. Remove the historical `DAV_` invented-key fallback.  
**Health:** stays **unverified** until a write smoke — do **not** set `verified_public` in this PR.

## Official source

| Piece | Value |
| --- | --- |
| Portal | http://www2.co.davidson.nc.us/DCInmates/ |
| Roster | `GET handler/inmate_data.ashx?dynamic=100&posStart=N&count=100` (dhtmlx XML) |
| Detail | `POST handler/inmate_details.ashx?number={InNum}&curbook={YY-######}` |
| Access | Ordinary public HTTP from datacenter egress (box recon 200). HTTPS to the same host EOF/handshake-fails from box — HTTP is the working public surface. No login, CAPTCHA, proxy, or stealth. |

## Fields (source-published only)

| Shamrock field | Source | Notes |
| --- | --- | --- |
| `Booking_Number` | roster cell[11] (e.g. `26-003404`) | Regex `^\d{2}-\d{5,8}$`. **Never** invent `DAV_*` from name/age. |
| `Person_ID` | roster cell[12] / row `@id` | Internal InNum for details. |
| `Full_Name` / parts | cells 0–3 | As published (HTML stripped from name cell). |
| `Booking_Date` | detail `Incarceration Date:` | First published incarceration timestamp. |
| `Charges` | detail Offense Description column | Joined with ` \| `. |
| `Bond_Amount` | Bail Bonds table **Totals / Remaining** | Digits only; if absent → `"0"` (not invented dollars). |

## Box recon evidence (2026-10-07)

- Roster `dynamic=100` → `total_count=311`, 100 rows; every row’s cell[11] matched `YY-######` (samples `26-003404`, `26-004682`, `24-000582`).
- `posStart=100&count=100` → next 100 distinct bookings.
- Detail `number=17942&curbook=26-003404` → Incarceration Date `7/13/2026 10:26:44 AM`, offense `POSSESS STOLEN MOTOR VEHICLE`, Bail Bonds Totals Remaining `$4,000.00`.

## Same-day NC queue holds (no PR)

| County | Hold reason |
| --- | --- |
| **Durham** | `www2.dconc.gov/sheriff/ips` bulk IPS is live (TLS OK) with name, Date Confined, charges, bond, and VINE/offender person IDs (`C01316`…). **No source booking/jail ID** — offender ID is person-level (multi-booking unsafe; same rule as Mecklenburg “never use PID alone”). Stay **fail_closed**. |
| **Wayne** | Registered `AgencyID=WayneCoNC` is **not** on Citizen Connect agency directory; index returns the agencies picker and `fetch_current_confinements` returns invalid/empty. CivicPlus CTA only. Stay hold / no fluff PR. |
| Wake / Forsyth / Cumberland / Onslow / Rowan / other P2C·Odyssey·DCN holds | Untouched. |

## Out of scope

- Promoting Davidson to `verified_public` before write smoke.
- Reopening Durham on person offender IDs.
