# Connecticut Scraper Registry

> Last updated: 2026-08-04  
> Palmetto surety footprint · Code: `scrapers/counties_ct/`

CT does **not** use a 8-county jail roster model like FL. Primary public sources:

| Scraper | Dashboard label | Portal | Status |
|---------|-----------------|--------|--------|
| `statewide_docket.py` | `Statewide (CT)` | [Criminal Dockets by Court](https://www.jud2.ct.gov/crdockets/SearchByCourt.aspx) | ✅ Live — ~1.6k docket rows / 12 courts per run |
| `ct_doc.py` | `CT DOC (CT)` | [CT Inmate Info Search](https://www.ctinmateinfo.state.ct.us/) | ✅ Live — full A–Z list (~14k inmates) + detail sample |

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
- **List-first** A–Z last-name search (Number, Name, DOB, Facility) — primary coverage
- Optional detail enrichment (bond + controlling offense) capped at 40/run (CC facilities preferred)
- `County` field is **`CT DOC`** (must match `REGISTERED_COUNTIES`)

## Gaps / non-goals
- No separate municipal jail scrapers for all 169 towns — DOC + court dockets cover statewide custody/hearings
- Housing courts omitted from docket list (civil focus)
