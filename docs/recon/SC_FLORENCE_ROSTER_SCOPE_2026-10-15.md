# Florence SC booking roster scope (2026-10-15)

**Question:** is `https://booking.fcso.org/index` (Florence County Detention Center, DevExpress letter-walk roster) custody-only?
**Answer:** **likely current-only, but not proven to the Orange standard.** Status stays `""`; presence never sets "In Custody".
**Method:** honest plain-HTTPS GETs (`scrapers.plain_http`, certificate checks on) of the portal page and its own `scripts/index.js`: two reads of the page plus the script, no searches or detail pages. No names or DOBs were recorded.

## Evidence
1. **Probe:** `GET /index` → HTTP **200** (11,559 bytes). No block.
2. **Page labels:** the heading is "Florence County Detention Center", with tabs "Inmates / Charges / Days" and a mode toggle **"Current" / "Released"**. "Current" (`option1`) is checked on `/index`.
3. **Released people are on separate pages.** `scripts/index.js` sends the "Released" toggle to `index-released`, with links to `inmates-released` and `charges-released`. The "Current" toggle stays on `index`. The scraper posts its letter searches to `index`, the Current page.
4. **No release fields on the roster.** Listing rows carry name, age, race, sex and admit date, with no release date. The code and fixtures (`tests/test_florence_sc_parser.py`) agree.
5. **Gap: no population figure.** Florence publishes no current-population number on the portal, so the roster count can't be checked against one. The Orange bar (labels and no release fields **and** a matching count) isn't met.

## Next step if CoS wants it
A published FCDC daily population (sheriff site or county report) compared with one letter-walk count would close the gap. Presence-based "In Custody" could then be added the way Orange's was.
