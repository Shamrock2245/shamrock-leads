# Manatee (FL/081) official-source recon, 2026-10-09

**Result: fail_closed.** The sheriff's only public roster is the Revize roster, which is challenged from every exit tried. The Clerk's court-records site is plain HTTPS and carries charges and bonds, but it is a court case index keyed by case number and OBTS number, not a booking roster with a source booking number. Under the current contract it does not qualify, so it is ranked as a candidate for a CoS/owner decision. The parser is kept for reopen (`SOURCE_CONTRACT_VALIDATED = False` in `scrapers/counties/manatee.py`), and the Mac relay skips the county (`ScraperScheduler.run_relay_only`; `scripts/manatee_residential_smoke.py` exits 4 without fetching).

## Trigger

On 2026-10-09 the relay runs got Cloudflare 403 on roster page 1 from both T-Mobile AS21928 and Comcast AS7922. They used stock Playwright Chromium, honest UA, code at ad35dc0. Nothing was written; status was `anti_bot`, `consecutive_failures` 10. The same path passed from the T-Mobile hotspot on 2026-09-22.

## Method

Checks ran from the agent box, 2026-10-09 3:20–3:35 PM ET, with plain `curl` over HTTPS and an honest UA (`ShamrockLeadsBot/1.0 (+https://shamrockbailbonds.biz; public jail roster source recon; plain HTTPS)`). Only status, content type, size, server, `cf-mitigated`, title and field labels were recorded. For the Clerk, one FELONY list page (filed 2026-10-07..08) and one case detail page were fetched. Only their field labels and counts were read; values were masked and nothing personal was stored or committed.

## Candidates (ranked)

| # | Candidate | Official? | Plain HTTPS result | Booking ID | Bond | Verdict |
|---|---|---|---|---|---|---|
| 1 | Revize roster `https://manatee-sheriff.revize.com/bookings` (current code) | Yes. MCSO embeds it as `<iframe id="arrests">` on `manateesheriff.com/arrest_inquiries_app/` | **403** `server: cloudflare`, `cf-mitigated: challenge`, "Just a moment..." on `/`, `/bookings`, `/api`, `/robots.txt`, `/sitemap.xml`, `/bookings.json`, `/bookings?format=json`, `/bookings.rss`, `/bookings.csv`. `/api/bookings`, `/api/v1/bookings` and `/api/inmates` give a 404 Laravel page with no challenge; no API route is linked or documented, and no further guessing was done. | Source `Booking #` (contract #113, proven 2026-09-22) | Not on roster (`""`) | **Blocked**: challenge from every exit tried (box, T-Mobile, Comcast) |
| 2 | Clerk CourtRecords case-type browse `https://records.manateeclerk.com/CourtRecords/Search/CaseType/{page}/{size}/{start}/{end}?caseTypeId=10` (FELONY; 35/37 MISDEMEANOR, 18 CRIMINAL TRAFFIC) | Yes (Clerk & Comptroller; linked from `manateeclerk.com/online-searches/`) | **200**, `Microsoft-IIS/10.0`, no Cloudflare, no CAPTCHA. The list is a GET by filing date (10 cases in the sample page). Detail is a POST to `/CourtRecords/Case/Details` with an anti-forgery token (`caseId`). The terms page has no automation prohibition. A banner reported a "database problem" at check time. | **None.** Detail labels include Case, Filed, Judge, Party (Name, DOB, Gender), Charges (Offense Date, Statute, Description, Degree, Citation, "Arrest Summons Served" date) and **OBTS number**. "Booking" appears 0 times. | **Yes.** A "Bonds" table (Bond Type, Active Amount) | **Not a roster.** Case number / OBTS are not booking keys under the rule. Cases appear only after filing (lag), and include non-custody filings. Possible later use: enrichment or an alternate contract, if CoS/owner rule on it. |
| 3 | Clerk hearing list `/CourtRecords/Search/Hearing` (e.g. FIRST APPEARANCE events) | Yes | Linked, plain HTTPS (not opened beyond the link) | Case numbers | — | Same limits as #2 |
| 4 | MCSO mobile app (`services/manatee_county_sheriff_s_office_application.php`) | Yes (vendor app) | Not probed: no documented public feed | Unknown | Unknown | Not pursued |
| — | `jail/charges___bond.php`, `jail/first_appearance.php`, `jail/intake___release.php` | Yes | 200, static information pages | none | none | Not data sources |

## What reopening needs

- **Revize roster:** a relay read smoke with a stock browser and honest UA and no challenge passed, then a Leads Ops write smoke. Then flip `SOURCE_CONTRACT_VALIDATED` and set Health / evidence back to `unverified`.
- **Clerk path:** an explicit CoS/owner decision that a court case number plus OBTS (with Clerk-published bond) is an acceptable key for Manatee leads. If approved, it needs its own scraper and tests: list by filing date, detail POST, bond `""` when the Bonds table is blank, never `"0"` unless the Clerk publishes a real 0. It would not provide a jail booking number, and lead timing would lag the arrest.

## Counters

`consecutive_failures` (10) came from blocked-exit runs. Manatee is exempt from auto-disable skips. With fail_closed, `BaseScraper.run` returns before any fetch and does not count a failure. To clear the stale streak, Leads Ops runs `python scripts/scraper_reenable.py "Manatee (FL)" --by leads-ops` (`MONGODB_URI` set).
