# Southern Software Citizen Connect: honest probe and roster scope (2026-10-15)

Base module: `scrapers/southern_sw_base.py`. Probes were minimal and honest: `scrapers.plain_http` with the honest User-Agent, certificate checks on, no impersonation, no proxy and no CAPTCHA solving. Each probe was one index GET plus one roster POST (page 1). Only counts and labels were recorded; no names or DOBs were logged.

| Agency | Index | JMSAgencyID | Roster POST (IDX=1) | Cards | Bond Total on card | BookingID | Source total label |
|---|---|---|---|---|---|---|---|
| DorchesterCoSC (verified SC) | 200 | present | 200 | 20 | 17/20 | 20/20 (data-bookingid) | "Total Inmates: 363" |
| HarnettCoNC | 200 | present | 200 | 20 | 16/20 | 20/20 (data-bookingid) | "Total Inmates: 305" |

## Roster finding
- The portal's own navigation lists **"Current Confinements"** separately from "Admits by Date Range" and "Releases by Date Range". The roster endpoint is `fetch_current_confinements.php`. This is a source-published current-only view, so its cards get "In Custody" with `status_source = "published_current_confinements_view"`.
- The `fetch_last7days.php` fallback lists admits, not custody, so its cards get Status "".
- Bond:
  - The card's "Bond Total:" is used when published. 3 to 4 of 20 cards had none.
  - Without it, the per-charge "Bond:" amounts are summed only when every charge item has one. Otherwise the bond is "".
  - A published 0 is kept. Amounts are formatted like "5000" (previously "5000.00").
- Charges come from published charge-item lines only. The free-text line heuristic is removed. Every probed card had charge items.
- The booking key is the source BookingID only (unchanged).

## Fail-closed
Both probed agencies answered honestly, so no county needs to move to fail_closed from these probes. The 12 AL and TN subclasses are already fail-closed guards that never fetch.
