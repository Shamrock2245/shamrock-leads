# Legacy P2C Source-Safety Registry

**Last reconciled:** 2026-08-14 EDT  
**Deployment evidence:** Shared guard commit `0de5f79` deployed successfully. The first Leads `/health` probe returned a transient 502, then recovered to 200; Sign, School, Paperwork, and Social `/auth` returned 200. Per-scraper Mongo persistence and alert delivery remain unproven.
**Purpose:** This registry is the operational source of truth for legacy P2C / CentralSquare wrappers reviewed during the all-state scraper hardening program. A registered job is not automatically a productive source. When a public bulk roster does not safely establish complete identity, a source-issued booking or inmate identifier, and a booking-time boundary, the code must **fail closed** and emit no arrest records.

The policy avoids access-control bypasses, unsupported blank or A–Z searches, synthetic booking identifiers, and inferred custody status. It also preserves the immutable state-and-county scope of every source identity.

| State | Jurisdiction | Existing path | Official source / replacement | Audited contract | Current policy |
|---|---|---|---|---|---|
| GA | Columbia | `columbia.py` | `columbiacountyso.org/inmate-inquiry/` | County custody list unavailable; legacy P2C access restricted | **Fail closed** |
| GA | Coweta | `coweta.py` | `cowetacosoga.policetocitizen.com/Inmates/Catalog` | P2C response access-restricted | **Fail closed** |
| GA | Dougherty | `dougherty.py` | County sheriff-linked P2C catalog | P2C response access-restricted | **Fail closed** |
| GA | Forsyth | `forsyth.py` | `forsythsheriffga.policetocitizen.com/Inmates/Catalog` | CAPTCHA/WAF-protected; no safe contract observed | **Fail closed** |
| GA | Hall | `hall.py` | `hallcounty.org/741/Inmate-Population-List` | Linked P2C response restricted; source-ID boundary unverified | **Fail closed** |
| GA | Spalding | `spalding.py` | `spaldingsheriff.org/pages/Social_Media_Info_P2C.html` | Broad roster lacks a source-issued booking/inmate ID and booking timestamp | **Fail closed** |
| NC | Alamance | `alamance.py` | `apps.alamance-nc.com/p2c/jailinmates.aspx` | Search-only P2C portal | **Fail closed** |
| NC | Cabarrus | `cabarrus.py` | `onlineservices.cabarruscounty.us/p2c/jailinmates.aspx` | Search-only P2C portal | **Fail closed** |
| NC | Cleveland | `cleveland.py` | Sheriff-linked CentralSquare P2C inquiry | Broad roster lacks a source-issued booking/inmate ID | **Fail closed** |
| NC | Forsyth | `forsyth.py` | `forsythsheriffnc.policetocitizen.com/Inmates/Catalog` | P2C response access-restricted | **Fail closed** |
| NC | Iredell | `iredell.py` | `p2c.iredellcountync.gov/jailinmates.aspx` | Search-only P2C portal | **Fail closed** |
| NC | New Hanover | `new_hanover.py` | Sheriff detention / OCV search | Search-oriented interface; no verified broad contract | **Fail closed** |
| NC | Union | `union.py` | `sheriff.unioncountync.gov/jailinmates.aspx` | CAPTCHA-protected; source-ID boundary unverified | **Fail closed** |
| NC | Lincoln | `lincoln.py` | `lincolnsheriff.org/inmateSearch` / OCV app `a46428092` | Complete public roster with names, source-issued Inmate IDs, and Booked Dates | **Repaired to OCV; deployed** |
| SC | Lee | `lee.py` | Sumter-Lee Regional CentralSquare portal | No validated broad roster contract | **Fail closed** |
| SC | Lexington | `lexington.py` | `lexingtonsheriffsc.policetocitizen.com/inmates` | Search-only P2C portal | **Fail closed** |
| TX | Johnson | `johnson.py` | Johnson County LEC inmate search | Search requires at least a last name and first-name character | **Recon only; no change** |

## Guard behavior

The shared `P2CBaseScraper` now has an explicit `SOURCE_CONTRACT_VALIDATED` switch. The fifteen wrappers marked **Fail closed** set it to `False`, include a non-PII source-safety reason, and return an empty record list **before any HTTP request or form submission**. This shared guard was deployed in `0de5f79`. Their scheduler registrations remain unchanged to keep dashboard and operations inventory truthful while preventing unsupported writes.

Lincoln is intentionally excluded from that guard set because its stale P2C transport was replaced by its verified official OCV feed. Johnson remains recon-only because its current search contract has not justified modifying the registered path.

## Revalidation criteria

A guarded path may be re-enabled only after a fresh official-source validation proves all of the following on a supported public broad-list contract: complete identity, a source-issued booking or inmate identifier, booking date or timestamp, county/state scope, and no CAPTCHA, WAF, login, or unsupported query bypass. The subsequent change must receive deterministic tests, a non-writing aggregate source smoke, normal deployment verification, and observed per-scraper persistence and alert telemetry before being marked productive.
