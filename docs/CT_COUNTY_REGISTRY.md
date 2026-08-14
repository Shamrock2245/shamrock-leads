# Connecticut Scraper Registry

> Last updated: 2026-08-04  
> Palmetto surety footprint · Code: `scrapers/counties_ct/`

CT does **not** use a 8-county jail roster model like FL. Primary public sources:

| Scraper | Dashboard label | Portal | Status |
|---------|-----------------|--------|--------|
| `statewide_docket.py` | `Statewide (CT)` | [Criminal Dockets by Court](https://www.jud2.ct.gov/crdockets/SearchByCourt.aspx) | ✅ Live — ~1.6k docket rows / 12 courts per run |
| `ct_doc.py` | `CT DOC (CT)` | [CT Inmate Info Search](https://www.ctinmateinfo.state.ct.us/) | ⏳ Fail closed — deployed 2026-08-14; official search is access-rejected by BITS BOT, so no records emit until a supported complete-identity, source-ID, and booking/admission-date bulk contract is verifiable. Public production hosts are healthy; no CT DOC writes or alerts are expected from the safety guard. |

## CLI

```bash
python main.py statewide          # CT criminal dockets (county key: Statewide)
python main.py "ct doc"           # may need exact scheduler key — prefer dashboard Run
# Scheduler job IDs typically: scraper_ct_* or bare county names Statewide / CT DOC
```

## Hardening notes (2026-08-04)

### Statewide docket
- **Requires** `curl_cffi` chrome impersonation (plain `requests` → SSL handshake failure)
- Form button value is **`Search`** (not Submit)
- Rotates **12** priority courts per run; full list of 35 in `ALL_COURTS`
- Skips empty placeholder table rows
- `MAX_ENTRIES_PER_COURT = 500`

### CT DOC
- The legacy A–Z broad-search path was retired because it disabled TLS verification, retained DOB, and could emit records without a verified booking or admission date.
- The official public search returned access rejection during validation. The registered path is therefore **fail closed** until a supported bulk contract exposes complete identity, a source-issued inmate or booking identifier, and a booking or admission date.
- `County` field remains **`CT DOC`** so the existing scheduler and dashboard identity stay stable; no duplicate municipal or statewide job was added.

## Gaps / non-goals
- No separate municipal jail scrapers for all 169 towns — DOC + court dockets cover statewide custody/hearings
- Housing courts omitted from docket list (civil focus)
