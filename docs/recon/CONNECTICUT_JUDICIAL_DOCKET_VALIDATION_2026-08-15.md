# Connecticut Judicial Docket Source-Contract Validation — 2026-08-15

> **Scope:** Metadata-only assessment of the Connecticut Judicial Branch criminal-docket source. No court record, case, person, image, date-of-birth, address, contact data, blank search, CAPTCHA workaround, proxy use, or browser workflow was retained or used. This assessment did not write arrest records, send alerts, or change any surety, payment, signing, or bond state.

## Finding

| Runtime scope | Source | Arrest-source contract result | Runtime decision |
|---|---|---|---|
| Statewide, Bridgeport, Hartford, New Haven, Stamford | [Connecticut Judicial Branch criminal docket](https://www.jud2.ct.gov/crdockets/SearchByCourt.aspx) | The shared implementation treats a judicial **docket number** and a **hearing date** as `Booking_Number` and court time. Those are not source-issued arrest booking identifiers or arrest-time fields. An ordinary metadata request also terminated at TLS transport before a public listing contract could be established. | **Fail closed** — no court docket query, court-case-as-arrest conversion, record write, score, alert, or client action is permitted. |

## Enforcement

`CTStatewideDockerScraper` now declares `SOURCE_CONTRACT_VALIDATED = False`, which the shared `BaseScraper.run()` gate enforces before any source retrieval or downstream processing. This inherited guard applies to the Statewide, Bridgeport, Hartford, New Haven, and Stamford court-docket jobs.

Re-enablement requires a Connecticut county or statewide **arrest** source—not only a criminal court docket—with a broad public listing that exposes a complete name, source-issued immutable booking or inmate identifier, booking or arrest date/time, and bounded pagination without access-control workarounds. It must then include parser tests, explicit source-state updates, and first-run persistence/alert telemetry.
