# JailTracker Source-Safety Registry

**Status:** Deployed shared guard — 2026-08-14

The JailTracker public application currently requires human verification before it exposes an offender roster. The shared `JailTrackerBaseScraper` therefore performs **no CAPTCHA solving, OCR, paid solver use, browser/API harvesting, proxying, automated submission, profile retrieval, DOB collection, mugshot collection, or synthetic identity construction**.

A dependent wrapper may emit a record only after a county-specific source assessment verifies, through normal public access, a broad roster that supplies all of the following: complete identity, a source-issued booking or inmate identifier, and a booking date/time. Until then, each listed wrapper remains registered for operational visibility but fails closed before network access.

| State | Guarded county paths | Current decision |
|---|---:|---|
| FL | Baker, Calhoun, Gulf, Holmes, Levy, Wakulla, Washington | Fail closed under shared JailTracker guard. |
| AL | Shelby | Fail closed under shared JailTracker guard. |
| GA | Dawson, Gordon, Pickens, Walker, Whitfield | Fail closed under shared JailTracker guard. |
| MS | DeSoto, Jones, Lauderdale, Madison | Fail closed under shared JailTracker guard. |
| SC | Chester, Greenwood | Fail closed under shared JailTracker guard. |
| TN | Blount, Maury, Rutherford, Williamson, Wilson | Fail closed under shared JailTracker guard. |

> **Sarasota, FL** subclasses the base but overrides `scrape()` with a separate source path; it is deliberately excluded from this shared guard and requires its own source-contract audit.

No county in this registry has per-scraper persistence or alert-delivery evidence from this guard. A later source-faithful repair must add deterministic mapping tests and a non-writing aggregate source smoke before any productive behavior is restored.
