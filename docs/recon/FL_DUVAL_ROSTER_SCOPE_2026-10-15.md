# Duval FL inmate search roster scope (2026-10-15)

**Question:** does `https://inmatesearch.jaxsheriff.org/` (JSO / ATIMS inmate search) list only people currently in custody?
**Answer:** **not proven.** Duval status is a published release or status field, otherwise `""`. Presence never sets "In Custody".
**Method:** code, docs and tests, plus one honest plain-HTTPS GET of the landing page (`scrapers.plain_http`, certificate checks on, no stealth or proxy, no gate click). No names or DOBs were recorded.

## Evidence
1. **Live probe:** the single GET failed certificate verification (`tls_cert_invalid: Duval certificate failed verification`). No page labels, headings or population figures could be read honestly, and there's no `verify=False` retry.
2. **Contract shape:** `duval.py` reads `releaseDate` / `release_date` / `releasedDate` / `dateReleased` from the intercepted search API, so the search results are built to carry released bookings. That points to mixed scope, not custody-only.
3. **Search model:** the scraper submits a last-name search (`"a"`) behind an ATIMS "I'm not a robot" gate. A name search over an inmate system can return past bookings; nothing in the repo shows it's restricted to current inmates.
4. **Docs:** `FL_67_STATUS_2026-10-08.md` lists Duval as `recon_only` / `unverified`, and `COUNTY_SOURCE_CONTRACT_MATRIX.md` says "Official current-roster contract not verified safely". There's no published current-population figure in the repo to compare against.

## Follow-up
The DrissionPage / ATIMS gate is out of scope here. If a future honest read shows a custody-only page label and a matching population count, presence-based "In Custody" can be revisited the way Orange's was.
