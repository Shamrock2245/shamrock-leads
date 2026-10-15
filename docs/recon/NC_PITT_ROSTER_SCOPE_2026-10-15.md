# Pitt NC Detainee Search roster scope (2026-10-15)

**Question:** is `https://apps.pittcountync.gov/apps/detention/detainee/` (ASP.NET Detainee Search, blank search plus GridView pager) custody-only, or does it publish a current-only label or view?
**Answer:** **not proven.** Status is `""` (`extra_data.status_source=""`). A published grid status, if one is ever added, wins (`published_status`). Charges and Bond_Amount are `""`, because the source publishes neither.
**Method:** one honest plain-HTTPS GET (`scrapers.plain_http`, certificate checks on), plus repo docs and code. No names or DOBs were recorded.

## Evidence
1. **Probe:** the GET failed with **`ConnectionError: Remote end closed connection without response`** (no HTTP status). No live labels could be read, and there was no retry or workaround.
2. **Page title (repo recon, 2026-09-25):** "Detainee Search" (`docs/NC_RECON_RESULTS.md`). There's no current-only label like Orange's "people currently in jail" and no Current/Released view like Florence's.
3. **"Every current detainee" is our wording, not the county's.** The module docstring and `COUNTY_SOURCE_CONTRACT_MATRIX.md` say a blank search "lists all current detainees". That's our own description from the 2026-09-25 read, not a source-published label.
4. **No release column:** the grid publishes Last/Suffix/First/Middle Name, Date of Birth, Booking Number, Gender, Race. Nothing shows a release in either direction.
5. **No population figure:** 477 rows / 48 pages were read on 2026-09-25 (`GAP_QUEUE_NC_TX_SC_2026-09-25.md`), but the county publishes no daily population to compare against.

## Next step if CoS wants presence-based custody
A live read of the page's own wording (or a published daily population for the Pitt County Detention Center), compared with one full grid count, would bring this up to the Orange or Florence standard.
