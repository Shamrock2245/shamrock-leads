# Pinellas (FL): fail closed pending Brendan's decision (2026-10-08)

**Decision needed (Brendan):** CoS rule for Pinellas (same as Lee #147): first try a direct plain-requests read with no patchright, stealth, proxy or captcha workaround. If it returns real keyed rows, strip patchright; if not, set `fail_closed` with evidence. The read did **not** return rows, so this PR sets Pinellas `fail_closed`. Reopening needs an allowed fetch path (see "Options" below).

## Source check
Run from the agent box on 2026-10-08 at about 11:42 AM ET with plain HTTPS requests (`curl`), an honest User-Agent (`ShamrockLeads/1.0 (+https://shamrockbailbonds.biz; public-records reader)`), 2 s between requests, no browser, no impersonation, no proxy. Status codes and response shape only; no personal data was received or recorded.

| Request | Status | Response |
|---|---|---|
| `GET https://whosinjail.pinellassheriff.gov/` | 200 | `text/html`, 5,971 bytes, IIS. Title `WhosInJailWebSite`. Loads `_framework/blazor.server.js` and calls `Blazor.start(...)`; one Blazor server-component marker. **0** `<form>`, **0** `<input>`, **0** `<table>` elements, **0** booking-number-like values (8+ digit runs). No challenge page, no 403. |
| `GET /api` | 200 | The same app shell (5,996 bytes): the SPA fallback route, not an API. |
| `GET /swagger/index.html` | 404 | empty |
| `GET /robots.txt` | 404 | empty |
| `GET https://www.pinellassheriff.gov/InmateBooking/` (legacy v1 ASP.NET roster) | 503 | IIS "Service Unavailable" |

## Why there is no plain-HTTP contract
Who's In Jail is a **Blazor Server** app. The booking-date search form, the roster table (booking number, inmate number, booking date/time, custody), and the Subject Charge Report modal (Offense Description, Bond Assessed) are rendered by the server over the app's SignalR circuit (`_blazor`), and only after the app's JavaScript runs. A plain GET or POST receives the empty shell. There is no published REST/JSON endpoint. Driving the SignalR circuit protocol by hand was not attempted: it is the app's internal render channel, not a public listing contract.

The current module gets rows only by launching **patchright** (a stealth Playwright fork) Chrome, which the no-stealth rule does not allow.

## What changes
- `scrapers/counties/pinellas.py`: `SOURCE_CONTRACT_VALIDATED=False` with a reason, so `BaseScraper.run()` stops before any source request. `scrape()` also returns `[]` without launching a browser for direct callers. The parser (`parse_charge_report_text`, bond rules from #142: unknown `""`, published `$0.00` kept) is kept unchanged so reopening is a small change.
- `dashboard/extensions.py`: `"Pinellas (FL)": "fail_closed"`.
- `docs/recon/county_source_contract_evidence.json` (FL/103): `fail_closed`, with the URL, posture and evidence above.
- `docs/recon/live_emitter_evidence.json`: the Pinellas row goes from `live_write` to `hold` (the builder refuses `live_write` on a `fail_closed` scope).
- Matrix regenerated; `FL_67_STATUS` (unverified 39→38, fail_closed 18→19) and `COUNTY_REGISTRY` updated.

## What does not change
- **Prod data:** nothing is written or deleted. Stored Pinellas rows stay as they are.
- **Lead views:** with #133, `fail_closed` counties drop out of the default lead list and the sellable seed. Picking Pinellas by name (or `include_fail_closed=true`) still shows stored rows. **Pinellas is FL volume rank 7, so new Pinellas leads stop until this is reopened.**

## Options for Brendan
1. Keep `fail_closed` until the county publishes a plain-HTTP listing or export.
2. Approve a non-stealth browser path (stock Playwright, honest UA, no proxy), e.g. as a Leads Ops relay job, and record it as an exception like the Broward/Lake SolveCaptcha approval.
3. Ask PCSO for a public data feed or export.
