# Orange County NC: roster scope and custody label (2026-10-15)

Minimal honest probe from the box via `scrapers.plain_http` (honest User-Agent,
certificate verification on, no proxy, no stealth, no CAPTCHA). No names, DOBs
or operator names were logged; only counts, sizes, labels and timestamps.

## Requests
1. `GET https://www.ocsonc.com/detention/current-detainees`: HTTP 200, ~630 KB.
   The page text carries the source's heading "Current Detainees" and links
   2 roster PDFs (Wix `usrfiles.com/ugd/...pdf`).
2. Both PDFs fetched once each: HTTP 200, `%PDF`.
   - Older link: report header dated 2026-03-23 07:56:22 (stale, ignored by the
     48h freshness gate).
   - Newest: 37,120 bytes, header 2026-10-15 06:39:41.

## Finding (newest report)
- Title line printed by the source: **"Daily In Custody Report by Facility"**,
  section "Confinement Reason: PRETRIAL/ARREST", facility "NEW JAIL".
  (The 2026-09-25 edition was titled "Detainees In Confinement Report by Facility".)
- That is a source-published current-only label, and the page linking it is titled
  "Current Detainees". So "In Custody" is set from the label, with
  `status_source = "published_in_custody_report_label"`. If the title line is
  missing (layout change or different report), Status is "".
- 101 detainee rows were parsed, each with a numeric `Bk #` (the source booking key; nothing is synthesized).
- The word "release" appears 0 times in the report.
- 28 of 101 rows have at least one charge with no `$` amount (blank or `NOBD`). For those rows Bond_Amount is
  now "" (unknown). It used to be "0", or a partial sum. A printed `$0` is kept.
- 0 rows had no charges. Charges is "" when none are printed (it was previously "Unknown").
