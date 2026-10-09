# Pinellas (FL): relay-only with a non-stealth browser (2026-10-08)

**Decision:** owner exception, recorded like the Broward/Lake SolveCaptcha exception. **Brendan, 2026-10-08 1:38 PM ET:** "we will connect at home, with residential egress." CoS agreed. Pinellas runs **only** through Brendan's home residential relay (Leads Ops) with a **non-stealth** stock Playwright Chromium and an honest bot User-Agent. It is **not** `fail_closed` and **not** `verified_public`: Health stays `unverified` until a Leads Ops write smoke goes through the relay. PR #155 stays on hold until Leads Ops confirms the relay can run stock headless Chromium.

History: the first version of #155 (earlier on 2026-10-08) set Pinellas `fail_closed`, following the CoS rule (try plain requests first; if no rows, fail closed for Brendan to decide). Brendan then chose the relay.

## Plain-HTTP evidence (kept)
Run from the agent box on 2026-10-08 at about 11:42 AM ET with plain HTTPS requests (`curl`), an honest User-Agent, 2 s between requests, no browser, no impersonation, no proxy. Status codes and response shape only; no personal data was received or recorded.

| Request | Status | Response |
|---|---|---|
| `GET https://whosinjail.pinellassheriff.gov/` | 200 | `text/html`, 5,971 bytes, IIS. Title `WhosInJailWebSite`. Loads `_framework/blazor.server.js` and calls `Blazor.start(...)`; one Blazor server-component marker. **0** `<form>`, **0** `<input>`, **0** `<table>` elements, **0** booking-number-like values. No challenge page, no 403. |
| `GET /api` | 200 | The same app shell (5,996 bytes): the SPA fallback route, not an API. |
| `GET /swagger/index.html` | 404 | empty |
| `GET /robots.txt` | 404 | empty |
| `GET https://www.pinellassheriff.gov/InmateBooking/` (legacy v1 roster) | 503 | IIS "Service Unavailable" |

Who's In Jail is a **Blazor Server** app: the booking-date search form, the roster table and the Subject Charge Report modal (Offense Description, Bond Assessed) are rendered over the app's SignalR circuit after its JavaScript runs. There is no plain-HTTP listing, so a browser is required. Driving the SignalR protocol by hand was not attempted (internal render channel, not a public contract).

## What the exception allows, and what it does not
- **Allowed:** stock Playwright **bundled Chromium**, headless, `--no-proxy-server`, proxy env vars stripped, an honest User-Agent naming the bot (`ShamrockLeadsBot/1.0 (+https://shamrockbailbonds.biz; ...)`), on the relay's own home-ISP (or iPhone hotspot) exit only.
- **Not allowed:** patchright (removed from the Pinellas path), stealth plugins or init scripts, Chrome UA spoofing or `channel="chrome"`, proxies of any kind, impersonation, challenge solving, runs from Hetzner/the VPS or the agent box.

## What changes in code
- `scrapers/counties/pinellas.py`: patchright and `playwright_launch_kwargs(channel="chrome")` are gone. `scrape()` first calls `resolve_egress` (`PINELLAS_EGRESS_MODE=direct`, the only mode): an unknown or non-residential exit raises `EgressBlocked` **before** any browser start or source request. Then the shared `launch_plain_browser()` (stock Playwright) and a context with `USER_AGENT`. The parser and the #142 bond rules are unchanged: the total is the sum of Bond Assessed only when every charge publishes an amount; a published `$0.00` is `"0"`; unknown is `""`, never `"0"`; a booking whose modal does not render is skipped.
- `config/relay_only.py`: `"Pinellas (FL)"` joins Manatee and Charlotte. `main.py` registers Pinellas without an interval, so the VPS scheduler never runs it and a dashboard trigger there is marked `relay_only`. `python main.py --relay-only` on the relay runs it.
- `scripts/pinellas_relay_smoke.py`: read smoke for the relay (aggregates only). Runbook: `docs/ops/PINELLAS_RELAY_RUN.md`.
- Health: no `SCRAPER_SOURCE_STATES` entry (default `unverified`), with the owner-exception comment. Evidence JSON (FL/103) `recon_only` with this posture; the live-emitter row stays `live_write` (2026-09-23 writes from the retired patchright path) with a relay note; matrix regenerated; `FL_67_STATUS` and `COUNTY_REGISTRY` updated.

## What does not change
No prod data is written or deleted. Stored Pinellas rows stay. Pinellas is not `fail_closed`, so it stays in the default lead list; new rows arrive only once the relay runs it.

## Relay prerequisites (Leads Ops)
`pip install -r requirements.txt` (playwright) and `python -m playwright install chromium` (on Linux, `--with-deps`). Then `PINELLAS_EGRESS_MODE=direct python scripts/pinellas_relay_smoke.py` (read only), then a write smoke with `python main.py Pinellas`.
