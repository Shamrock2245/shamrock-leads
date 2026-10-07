# FL idle eight — Citrus, Clay, Columbia, Gilchrist, Hamilton, Madison, Okaloosa, Okeechobee (2026-10-07)

**Scope:** registered Florida scrapers with no recent emit evidence (ranked PR #2 after FL bond/charges hydrate).
**Method:** plain HTTP(S) from the agent box (datacenter egress, Python `requests` / stdlib; no TLS impersonation, stealth browser, proxy, Obscura, or CAPTCHA/WAF bypass).
**Privacy:** no personal data recorded. Booking-key formats shown as patterns only.
**Write smoke:** `MONGODB_URI` was not available here. Read smokes only. Do **not** promote to `verified_public` until a Mac/VPS write smoke lands.

## Summary

| County | Official source | Box access | Source booking key | Outcome |
|---|---|---|---|---|
| **Hamilton** | https://inmate.hamiltonsheriff.com/smartwebclient/jail.aspx | 200, plain HTTPS SmartWEB | `Booking No` → `HCSO<YY>JBN<NNNNNN>` | **Fixed** — URL was NXDOMAIN; Suwannee-style booking-date window |
| **Madison** | https://smartweb.mcso-fl.org/smartwebclient/jail.aspx | 200, plain HTTPS SmartWEB | `MCSO<YY>JBN<NNNNNN>` | **Fixed** — prior host was wrong tenant / expired cert |
| **Gilchrist** | https://inmate.gcso.us/smartwebclient/Jail.aspx (from gcso.us) | 200, plain HTTPS SmartWEB | `GCSO<YY>JBN<NNNNNN>` | **Fixed** — prior smartcop host NXDOMAIN |
| **Citrus** | https://www.sheriffcitrus.org/public_info/recent_arrest.php → dated PDF | 200 + PDF download | `AR #` → `AAYY-NNNNNN` | **Fixed** — plain requests + pdfplumber table parse (no DrissionPage) |
| **Okaloosa** | https://okaloosacountyjail.myokaloosa.com/InmateLocator/Default.aspx | 200 ASP.NET form | `Booking#` → 10-digit `YYYY######` | **Fixed** — SPA root is not the roster; Default.aspx A–Z search |
| **Clay** | https://www.sheriffclayco.org/divisions/detention/detention-listings/ | 200 HTML table ~400 rows | **None** (Name / Booking Date only) | **fail_closed** — no source booking ID; do not invent name keys |
| **Columbia** | legacy `http://50.204.15.10/smartwebclient/Jail.aspx` | **503**; SO site has no replacement link | n/a | **fail_closed** — no invented portal |
| **Okeechobee** | https://www.okeesheriff.org/inmate-search | 200 Wix shell | n/a | **fail_closed** — no roster feed |

## Read-smoke notes (patterns only)

- Hamilton / Madison / Gilchrist: booking-date window POST returns cards with matching `bookno=` image + `Booking No:` text; charges and bond present on cards. Shared helper: `scrapers/fl_smartweb.py`.
- Citrus: PDF table header `Photo | Name | AR # | Date | Arrest Type | Offense | DOB | Bond` (~140 rows across pages in the dated file observed 2026-10-07).
- Okaloosa: Default.aspx last-name search returns discrete columns including `Booking#` / `SPN#` / name / demographics. Bail amount lives on the detail pane (not required for listing contract).
- Clay: public roster confirmed; incomplete without booking ID.
- Columbia: FL DOS county-jail directory lists Columbia with an empty inmate-search cell; confirms no official public URL found.
- Okeechobee: Wix page only.

## Holds / next steps

1. Mac or VPS **write smoke** for Hamilton, Madison, Gilchrist, Citrus, Okaloosa → then `SCRAPER_SOURCE_STATES` → `verified_public` + `live_emitter_evidence.json`.
2. Clay stays fail_closed until the SO publishes a source booking / inmate ID on the listing (or a detail page with an ordinary public ID).
3. Columbia / Okeechobee stay fail_closed until a plain public roster URL is proven — do not invent hosts.
