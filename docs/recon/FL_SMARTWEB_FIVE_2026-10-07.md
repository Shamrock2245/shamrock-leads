# FL SmartWEB five — Bradford, Dixie, Taylor, Escambia, Santa Rosa (2026-10-07)

**Scope:** Florida SmartCOP SmartWEB JAIL View counties that still used `curl_cffi` / invented-key fallbacks while ordinary public access already exposes source Booking No.
**Method:** plain HTTP(S) from the agent box (datacenter egress, Python `requests`; no TLS impersonation, stealth browser, proxy, Obscura, or CAPTCHA/WAF bypass).
**Privacy:** no personal data recorded. Booking-key formats shown as patterns only.
**Write smoke:** `MONGODB_URI` was not available here. Read smokes only. Do **not** promote to `verified_public` until a Mac/VPS write smoke lands. Health stays **unverified**.

## Summary

| County | Official source | Box access | Source booking key | Outcome |
|---|---|---|---|---|
| **Bradford** | http://smartweb.bradfordsheriff.org/smartwebclient/Jail.aspx | 200 HTTP SmartWEB | `BCSO<YY>JBN<NNNNNN>` | **Fixed** — shared `fl_smartweb`; dropped name/date invented keys |
| **Dixie** | https://smartcop.dixiecountysheriff.com/smartwebclient/Jail.aspx | 200 HTTPS SmartWEB | `DCSO<YY>JBN<NNNNNN>` | **Fixed** — TypeSearch-less form accepted |
| **Taylor** | http://smartcop.taylorsheriff.org:8989/SmartWEBClient/Jail.aspx | 200 HTTP :8989 SmartWEB | `TCSO<YY>JBN<NNNNNN>` | **Fixed** — TypeSearch-less form accepted |
| **Escambia** | https://inmatelookup.myescambia.com/smartwebclient/jail.aspx | 200 HTTPS SmartWEB | `ECC<YY>JBN<NNNNNN>` | **Fixed** — curl_cffi retired; AddMoreResults pages |
| **Santa Rosa** | https://jailview.srso.net/SmartWebClient/jail.aspx | 200 HTTPS SmartWEB | `SRSO<YY>JBN<NNNNNN>` | **Fixed** — first-page cards emit; AddMoreResults may 500 |

## Read-smoke notes (patterns only)

- Shared helper: `scrapers/fl_smartweb.py` (also used by Hamilton / Madison / Gilchrist).
- Parser hardened for SearchHeader identity lines `(W/ FEMALE )` and `(W/ FEMALE / DOB: … )`, and strips `Enlarge Photo` link text so Full_Name is source-clean.
- TypeSearch ("Current Inmates Only") is optional — Dixie/Taylor builds omit it; booking-date window still works.
- Bradford / Dixie / Taylor / Santa Rosa: `AddMoreResults` returned HTTP 500 from box on 2026-10-07; first-page cards still carry matching `bookno=` + `Booking No:` text. Escambia paginated successfully (147 bookings in a 7-day window).
- **Superseded later 2026-10-07** by [`FL_SMARTWEB_LEGACY_PAGING_2026-10-07.md`](./FL_SMARTWEB_LEGACY_PAGING_2026-10-07.md): those 500s were the legacy page-method build rejecting the modern `{searchVals: …}` wrapper. `fl_smartweb` now detects the build and pages all five.
- Rows without a matching source Booking No are dropped. No name/date synthetic keys.

## Holds / next steps

1. Mac or VPS **write smoke** for all five → then `SCRAPER_SOURCE_STATES` → `verified_public` + `live_emitter_evidence.json`.
2. Do **not** reopen JailTracker FL (Baker/Calhoun/Gulf/Holmes/Levy/Wakulla/Washington), Sarasota, Clay (no booking ID), Columbia, Okeechobee, Leon, or Gadsden in this PR.
3. Charlotte / Manatee remain residential-gated (Leads Ops). Idle-eight five (Hamilton/Madison/Gilchrist/Citrus/Okaloosa) still await write smoke from #95.
