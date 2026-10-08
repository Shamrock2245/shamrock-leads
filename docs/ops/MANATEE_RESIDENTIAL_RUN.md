# Manatee FL: residential run (Leads Ops)

Manatee's public roster (`https://manatee-sheriff.revize.com/bookings`) sits behind Cloudflare. Datacenter exits get an HTTP 403 `cf-mitigated: challenge` page; the agent box got this on 2026-10-07. The VPS and the office Comcast line have also been blocked in the past.

**Manatee runs only on Brendan's home relay, which Leads Ops operates.** The scraper runs *on* that host and uses the host's own home-ISP exit. "Residential" means that connection. It is **not** a proxy service.

## What was removed (2026-10-07, owner decision via CoS)

- The APE/Warren residential proxy and office/Tailscale SOCKS resolver (`MANATEE_EGRESS_MODE=auto`). Setting `auto` now raises a config error.
- The stealth browser launcher (Patchright and the stealth context with init-script patches). Manatee now launches stock Playwright Chromium, headless, with `--no-proxy-server` and with every `*PROXY*` environment variable removed from the browser's environment.
- Proxy success/failure bookkeeping for Manatee.

Proxy environment variables (`HTTP(S)_PROXY`, `ALL_PROXY`, `SCRAPER_SOCKS_PROXY`, `WARREN_*`) are ignored by Manatee. The exit-IP check runs with `trust_env=False`. `tests/test_manatee_no_proxy_path.py` proves no proxy, SOCKS, APE or stealth helper is reachable. Do not add proxy credentials, CAPTCHA solvers or stealth tooling for Manatee. Charlotte got the same cleanup later on 2026-10-07 (`tests/test_charlotte_no_proxy_path.py`).

## What the scraper does now

| Situation | Result | Health / status |
|---|---|---|
| Host exit is not verified US residential (datacenter, VPN, non-US, or the org/country lookup failed) | `EgressBlocked` before any browser starts | `error`, class `anti_bot`, message starts `egress_block:` |
| Page still shows the Cloudflare challenge/block after a passive 45 s wait (no clicks, no solving) | `EgressBlocked`, nothing written | same |
| Missing table or column, unknown `Released` value, empty or repeated page, `MAX_PAGES` stop, total mismatch, booking-number collision | `ParseDriftError`, nothing written | `error`, class `parse_drift` (alerts #scraper-errors) |
| Roster read cleanly | records written (bond blank, never `0`) | `ok` |

An egress block is never reported as `empty` or `ok`.

## Relay-only scheduling

Manatee is **relay-only** (`config/relay_only.py`, together with Charlotte). The VPS/Hetzner scheduler keeps Manatee registered but never gives it an interval job. A dashboard run-now or custody-recheck trigger for Manatee is marked `relay_only` on the VPS and is not run there. Leads Ops runs it from the relay with `python main.py --relay-only`, which runs Manatee and Charlotte once each and exits non-zero if either fails. `python main.py Manatee` runs Manatee alone. See `docs/ops/REVIZE_RELAY_RUN.md` for the shared runner.

## Egress setting

`MANATEE_EGRESS_MODE` defaults to `direct`, and `direct` is the only accepted value. Any other value raises a config error.

## Run it on the home relay

1. Make sure the relay host's VPN is off so its exit is the home ISP.
2. From the repo checkout (with Playwright Chromium installed: `python -m playwright install chromium`), run a **read smoke**. It writes nothing and prints no names:

   ```bash
   MANATEE_EGRESS_MODE=direct python scripts/manatee_residential_smoke.py
   ```

   - exit `0`: JSON with `bookings`, `unique_booking_numbers`, `duplicate_booking_numbers` (must be 0), `status`, `with_charges`, `multi_charge`, `bond_unknown`, `booking_number_lengths` and `walk` (pages, rows, published total).
   - exit `2`, `egress_block`: the exit is not verified residential, or Cloudflare did not clear for a stock browser. Check the VPN and retry once later. Do not loop, and do not reach for a proxy or a stealth browser.
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
- whether the stock (non-stealth) browser clears Cloudflare from the relay; if it consistently gets `egress_block`, report it rather than changing the browser,
- the detail-page fixture capture below.

## Detail-page fixture capture (for the bond / statute / degree / DOB / address follow-up)

The box cannot reach `/bookings/<id>`, so the parser must be built from real pages saved on the relay. Capture once, from the relay, with a normal browser session and no proxy:

- **URL pattern:** `https://manatee-sheriff.revize.com/bookings/<Booking #>`, using the same id as the roster's `Booking #` link.
- **Sample:** 12 bookings from page 1 of the roster, chosen to cover: at least 3 with several charges, at least 2 marked released, at least 2 with any visible no-bond / hold wording, and at least 1 booked in the last 24 h. Also save roster page 1 and page 2 themselves.
- **Save the raw HTML** of each page as served (browser "Save Page As → HTML only", or `page.content()`), named `manatee_detail_<Booking #>.html` and `manatee_roster_p<N>.html`. Also save one full-page screenshot per detail page. Record the capture time (ET) and the HTTP status.
- **Handling:** these files contain real names, DOBs and addresses. Keep them off GitHub and out of chat. Hand them to Scraper Watch through a private channel that Chief of Staff names. Scraper Watch will turn them into synthetic, field-shaped test fixtures before anything is committed.
- **If a detail page shows a Cloudflare challenge from the relay,** note that and stop. Do not retry in a loop.
