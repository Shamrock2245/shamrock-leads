# South Carolina and North Carolina Source Validation — 2026-08-14

> **Scope:** Current public-source checks for South Carolina and North Carolina scraper work. A portal is eligible only where a public, source-provided identifier can be used as the immutable `County + Booking_Number` key. This note records validation status only; it does not confer production status.

| County | Source outcome | Public source key | Decision |
|---|---|---:|---|
| Newberry, SC | The official county inmate-search page is temporarily unavailable as an interactive search but dynamically links a Sheriff uploads PDF containing current booking information. | Yes — visible `SO`-prefixed identifier | Qualified; dynamic official-PDF scraper implemented and aggregate source probe returned 21 records with valid source keys. |
| Alexander, NC | The official Sheriff-linked P2C catalog is current but its public listing did not establish a booking, inmate, detention, case, or court identifier. | No | Do not ingest. |
| Cherokee, NC | The county-hosted DCN grid is reachable but exposes only descriptive fields and no stable identifier. | No | Do not ingest. |
| Spartanburg, SC | The official Sheriff home page links booking search, but the available booking host did not yield a verified public response or identifier without protected-access workarounds. | Not established | Blocked pending ordinary browser-accessible schema verification. |
| Darlington, SC | The documented DCN endpoint did not provide a current browser- or TLS-verifiable roster schema. | Not established | Do not ingest. |
| Wilson, NC | The official Sheriff site exposes an iSOMS inmate-search form but its unqueried public shell did not establish a stable record identifier. | Not established | Do not ingest until an ordinary public detail schema is verified. |
| Columbus, NC | The official Sheriff in-custody page links a public Dropbox report folder; the report schema and its stable source identifier remain unverified. | Not established | Do not ingest. |
| Jackson, NC | The official detention page's “Current Inmate Search” link currently resolves back to the Sheriff home page, not a public roster endpoint. | No | Blocked. |

## Source-key enforcement

The shared scraper runner now rejects known historical local booking-number fallbacks before scoring, writing, alerts, or scraper-health metrics. Buncombe and Johnston, NC no longer construct unprefixed hash values when a source booking identifier is unavailable. These changes preserve the platform requirement that the natural key must originate with the source rather than a name, date, or hash.

## Next gate

Before adding another SC or NC county, verify an accessible official source that visibly provides a unique booking, inmate, detention, case, or court identifier. Do not bypass CAPTCHAs, WAFs, logins, app-only restrictions, or access controls.
