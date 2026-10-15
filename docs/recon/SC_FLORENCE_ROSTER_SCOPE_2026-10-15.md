# Florence SC booking roster scope (2026-10-15)

**Question:** is `https://booking.fcso.org/index` (Florence County Detention Center, DevExpress letter-walk roster) custody-only?
**Answer:** the roster is the county's own **"Current"** view; released people are on separate `*-released` pages. **CoS decision (b), 2026-10-15:** rows from the Current view (`/index`, `inmates`, `charges`) get `Status="In Custody"` with `extra_data.status_source="published_current_label"`. Rows from `index-released` / `inmates-released` / `charges-released`, or from any unknown origin, get `""`. A published detail status, if one ever appears, wins (`status_source="published_status"`). Origin comes from the URL of the response the row was parsed from, not assumed.
**Method:** honest plain-HTTPS GETs (`scrapers.plain_http`, certificate checks on) of the portal page and its own `scripts/index.js`: two reads of the page plus the script, no searches or detail pages. No names or DOBs were recorded.

## Evidence
1. **Probe:** `GET /index` → HTTP **200** (11,559 bytes). No block.
2. **Page labels:** the heading is "Florence County Detention Center", with tabs "Inmates / Charges / Days" and a mode toggle **"Current" / "Released"**. "Current" (`option1`) is checked on `/index`.
3. **Released people are on separate pages.** `scripts/index.js` sends the "Released" toggle to `index-released`, with links to `inmates-released` and `charges-released`. The "Current" toggle stays on `index`. The scraper posts its letter searches to `index`, the Current page.
4. **No release fields on the roster.** Listing rows carry name, age, race, sex and admit date, with no release date. The code and fixtures (`tests/test_florence_sc_parser.py`) agree.
5. **No population figure.** The county publishes **no** current-population number (not on the portal, and none found for FCDC), so the roster count can't be checked against one. That's the difference from Orange (2,919 roster vs 2,976 published). CoS accepted the county's own Current/Released labels and page separation as the evidence (decision (b)).

## Limits
- If the county ever mixes released people into the Current view, these rows would be wrong until a detail page publishes a status. A published status wins.
- Rows from an unrecognized page path, a different host, or a response with no URL get `""`.
