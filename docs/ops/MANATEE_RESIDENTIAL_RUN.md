# Manatee FL: residential run (Leads Ops)

Manatee's public roster (`https://manatee-sheriff.revize.com/bookings`) sits behind Cloudflare. Datacenter exits get an HTTP 403 `cf-mitigated: challenge` page; the agent box got this on 2026-10-07. Historically the VPS and the office Comcast line have also been blocked, and Manatee has only written over the iPhone hotspot or another residential connection. Treat the **iPhone hotspot as the default path**. A home-ISP connection is fine if its read smoke passes.

"Residential" here means that Mac or hotspot network. It is **not** a proxy service. Do not add proxy credentials, CAPTCHA solvers or stealth tooling for Manatee.

## What the scraper does now

| Situation | Result | Health / status |
|---|---|---|
| Host exit is not US residential (or its org/country lookup failed) | `EgressBlocked` before any page load | `error`, class `anti_bot`, message starts `egress_block:` |
| Page still shows the Cloudflare challenge/block after 45 s | `EgressBlocked`, nothing written | same |
| Missing table or column, unknown `Released` value, empty or repeated page, `MAX_PAGES` stop, total mismatch, booking-number collision | `ParseDriftError`, nothing written | `error`, class `parse_drift` (alerts #scraper-errors) |
| Roster read cleanly | records written (bond blank, never `0`) | `ok` |

An egress block is never reported as `empty` or `ok`.

## Egress setting

`MANATEE_EGRESS_MODE` (default `auto`):

- `direct`: use for Mac and hotspot runs. No proxy is resolved. The host's own exit must look US residential (known ISP org, country US), otherwise the run stops with `egress_block`.
- `auto`: the existing resolver (env SOCKS, then APE/Warren, then office/Tailscale SOCKS, then direct only if this host is residential). This is what the VPS scheduler uses. With none of those healthy, the VPS run fails loudly with `egress_block`, which is expected.

Any other value raises a config error.

## Run it from the Mac or hotspot

1. Turn the VPN off. Join the iPhone hotspot (or a home ISP that has passed before).
2. From the repo checkout (with Playwright/Chrome installed, as for Charlotte), run a **read smoke**. It writes nothing and prints no names:

   ```bash
   MANATEE_EGRESS_MODE=direct python scripts/manatee_residential_smoke.py
   ```

   - exit `0`: JSON with `bookings`, `unique_booking_numbers`, `duplicate_booking_numbers` (must be 0), `status`, `with_charges`, `multi_charge`, `bond_unknown`, `booking_number_lengths` and `walk` (pages, rows, published total).
   - exit `2`, `egress_block`: the exit is not residential, or Cloudflare did not clear. Check the VPN, switch networks (home ISP or hotspot) and retry once. Do not loop.
   - exit `3`, `parse_drift`: the roster changed shape. Send the JSON line to Scraper Watch. Do not work around it.
3. **Write smoke** (prod), only after a clean read smoke and with `MONGODB_URI` set:

   ```bash
   MANATEE_EGRESS_MODE=direct python main.py Manatee
   ```

   Confirm in the dashboard that the new Manatee rows have real `Booking #` keys, `bond_amount_raw` blank, charges filled, and Health `ok`. Only then can Chief of Staff move Manatee toward `verified_public`.

## Things to record from the first residential run

Report these back so the contract can be tightened:

- the exact roster header text and any pagination text ("Showing x to y of N"),
- whether a booking with several charges appears on several rows,
- what the `Released` column contains,
- whether `/bookings/<id>` detail pages load from residential egress. If they do, they may carry bond, statute and degree; that is a follow-up and is not scraped today.
