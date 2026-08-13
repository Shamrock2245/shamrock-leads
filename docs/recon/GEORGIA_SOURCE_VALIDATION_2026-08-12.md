# Georgia Source Validation — 2026-08-12

> **Scope:** Current public-roster validation for possible Georgia scraper additions. This note records source behavior only; it does not confer production status.

| Candidate | Current source result | Stable booking identifier visible | Decision |
|---|---|---:|---|
| McDuffie / EAS | The documented `offenderindex.com/mcduffiecoga/` endpoint returned HTTP 404. The separately documented InteropWeb page presents current-custody details but no visible booking or inmate identifier. | No | Do not schedule or ingest. |
| Clayton | The official county inmate-search page is live and embeds a current search experience with name and 48-hour, 14-day, and 31-day booking-date options. No results query was submitted and no access controls were bypassed. A booking or inmate identifier was not established from the page shell. | Not established | Do not build until the public result schema is verified without bypassing safeguards. |
| Colquitt | Official sheriff information did not expose a current online roster with a reliable public identifier. | No | Blocked. |
| Burke | Official sheriff materials link to a live InteropWeb roster, but the reviewed listing exposes no stable public booking or inmate identifier. | No | Blocked. |
| Baldwin | No qualifying Georgia public roster endpoint was established. | No | Blocked. |
| Stephens | No qualifying Georgia public roster endpoint was established. | No | Blocked. |

## EAS handling

The repository's EAS county list is retained only as a manual reconnaissance utility. It is intentionally **not scheduled** and must not feed production writers until each endpoint is current and a stable booking identifier is demonstrated. This preserves the `County + Booking_Number` deduplication requirement and prevents stale or synthetic identifiers from being treated as arrest records.

## Next validation gate

A new wrapper may proceed only when the source is current, public, rate-safe, and exposes a unique booking or inmate identifier that can be mapped directly to `Booking_Number`. If a source requires a CAPTCHA or other access control, do not bypass it; record the block and select another source.
