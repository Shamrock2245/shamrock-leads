# Charlotte (FL/015) official-source recon, 2026-10-09

**Result: fail_closed.** No official source reachable with plain HTTPS (or a stock browser) publishes a public booking roster with a real source booking number. The parser is kept for reopen (`SOURCE_CONTRACT_VALIDATED = False` in `scrapers/counties/charlotte.py`), and the Mac relay skips the county (`ScraperScheduler.run_relay_only`; `scripts/charlotte_residential_smoke.py` exits 4 without fetching).

## Trigger

On 2026-10-09 the relay runs got Cloudflare 403 on roster page 1 from both T-Mobile AS21928 and Comcast AS7922. They used stock Playwright Chromium, honest UA `ShamrockLeadsBot/1.0`, code at ad35dc0. Nothing was written; status was `anti_bot`, `consecutive_failures` 7. The same path passed from the T-Mobile hotspot on 2026-09-22. Getting the same block from both a mobile and a residential exit points to fingerprint or path rules, not egress. Either way a challenge is a stop.

## Method

Checks ran from the agent box, 2026-10-09 3:20–3:35 PM ET, with plain `curl` over HTTPS and an honest UA (`ShamrockLeadsBot/1.0 (+https://shamrockbailbonds.biz; public jail roster source recon; plain HTTPS)`). Only status, content type, size, server, `cf-mitigated`, page title and link/label shape were recorded; no personal data. A Cloudflare challenge was recorded and left alone. One request per URL, with a 1s spacing on the roster host.

## Candidates (ranked)

| # | Candidate | Official? | Plain HTTPS result | Booking ID | Bond | Verdict |
|---|---|---|---|---|---|---|
| 1 | Revize roster `https://inmates.charlottecountyfl.revize.com/bookings` (current code) | Yes. CCSO embeds it as an `<iframe>` on `ccso.org/correctional_facility/local_arrest_database.php` ("Local Arrest Database") | **403** `server: cloudflare`, `cf-mitigated: challenge`, title "Just a moment..." on `/`, `/bookings`, `/robots.txt`, `/sitemap.xml`, `/bookings.json`, `/bookings?format=json`, `/bookings.rss`, `/bookings.csv`. `/api/bookings` gives a 404 Laravel page with no challenge, and no API route is linked or documented. | Source `Booking #`, contract proven 2026-09-22 | Not on roster (`""`) | **Blocked**: challenge from every exit tried (box, T-Mobile, Comcast) |
| 2 | Clerk Benchmark case search `https://courts.charlotteclerk.com/Benchmark/Home.aspx/Search` (linked from `charlotteclerk.com/courts/criminal`) | Yes (Clerk of Court) | 200, no Cloudflare. The search form loads Google reCAPTCHA (`g-recaptcha`, `data-sitekey`, `recaptcha/api.js`) | Case / citation numbers, not booking numbers | Not checked (behind CAPTCHA) | **Stop**: CAPTCHA, not solved by rule; and not a booking roster |
| 3 | Clerk portal `https://clerkportal.charlotteclerk.com/` | Yes | 200 (`server: cloudflare`, no challenge); login / public-records-request portal | n/a | n/a | Not a roster |
| 4 | CCSO mobile app (OCV, `com.ocv.charlotteflsheriff`, linked from ccso.org) | Yes (vendor app) | Not probed: no documented public feed is linked. An app's private API is not a sanctioned public path. (Hendry's OCV feed had only a person-level id, #143.) | Unknown | Unknown | Not pursued |
| — | FDLE sexual offender search (linked) | State | n/a | n/a | n/a | Not a jail roster |

Other CCSO pages checked are informational only, with no roster or export: `correctional_facility/inmate_contact.php`, `inmate_funds.php`, `warrants.php`, and `divisions/bureau_of_detention/*`. The sheriff site itself is Revize CMS (no challenge on `www.ccso.org`). Its only roster is the challenged iframe.

## Reopen conditions

Reopen only when a relay read smoke passes on the Revize roster with a stock browser, no challenge passed and the honest UA, followed by a Leads Ops write smoke. That means flipping `SOURCE_CONTRACT_VALIDATED` back and the Health / evidence rows to `unverified`. An owner-arranged official data feed from CCSO (e.g. a published export or an API key) would also qualify. No stealth, proxy, CAPTCHA solving or impersonation.

## Counters

The current `consecutive_failures` (7) came from blocked-exit runs. Charlotte is in `AUTO_DISABLE_EXEMPT_LABELS`, so it was never skipped. With fail_closed, `BaseScraper.run` returns before any fetch and does not count a failure. To clear the stale streak, Leads Ops runs `python scripts/scraper_reenable.py "Charlotte (FL)" --by leads-ops` (`MONGODB_URI` set).
