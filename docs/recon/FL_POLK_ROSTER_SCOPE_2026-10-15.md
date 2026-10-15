# Polk FL jail inquiry roster scope (2026-10-15)

**Question:** is Polk's source (`https://www.polksheriff.org/detention/jail-inquiry/SearchJail/`) custody-only?
**Answer:** **no, it's mixed.** Polk status is a published release date (`Released`) or status field, otherwise `""`. Presence never sets "In Custody".
**Method:** code and tests, plus one honest plain-HTTPS POST (certificate checks on, counts only; no names or DOBs recorded).

## Evidence
1. **Booking-date search, not a roster.** `polk.py` posts `SelectedSearchType=BookingDate` for each of the last `DAYS_BACK` days. Results are everyone booked on that date, including people since released.
2. **Release field on every row.** SearchJail rows carry `ReleaseDate`, and the module already maps a non-blank one to `Released`. A custody-only roster wouldn't need it.
3. **No current-population comparison possible.** The single honest probe (2026-10-15, one booking date, counts only) got **`egress_block: Polk connection_reset`** (TLS handshake dropped before certificate verification). No live labels or counts were read, and there was no retry or workaround.
4. **Docs:** `FL_67_STATUS_2026-10-08.md` lists Polk as `recon_only` / `unverified`.

So a row without a `ReleaseDate` may be in custody, or may be a release the source hasn't posted. It stays `""`.
