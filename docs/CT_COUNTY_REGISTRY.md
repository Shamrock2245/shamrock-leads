# Connecticut Scraper Registry

> **Last Updated:** 2026-07-24  
> **Registered (dashboard):** 2 scrapers — Statewide Criminal Dockets + Statewide DOC Inmate Roster  
> **Package:** `scrapers/counties_ct/`  
> **Job IDs:** `scraper_ct_statewide`, `scraper_ct_doc` · CLI: `.venv/bin/python main.py ct_doc`

## System Architecture

Connecticut criminal justice is unified statewide:
1. **CT Judicial Branch Criminal Dockets (`statewide_docket.py`)**: Daily court dockets covering all 8 Judicial Districts and Geographical Area courts (Bridgeport, Hartford, New Haven, Waterbury, Stamford, New Britain, Danbury).
2. **CT Department of Correction Inmate Roster (`ct_doc.py`)**: Real-time inmate lookup covering all CT state correctional centers (Bridgeport CC, Hartford CC, New Haven CC, Corrigan-Radgowski, MacDougall-Walker, York CI, Brooklyn CI).

---

## Registered & Operational Scrapers

| Scraper | Coverage | Package File | Strategy / Target | Status | Notes |
|---------|----------|--------------|-------------------|--------|-------|
| **Statewide Criminal Dockets** | All 8 Judicial Districts + GAs | `statewide_docket.py` | https://www.jud2.ct.gov/crdockets/SearchByCourt.aspx | ✅ Live | ASP.NET WebForms; `curl_cffi` TLS impersonation (`chrome124`); extracts daily criminal dockets |
| **CT DOC Inmates** | All CT Correctional Facilities | `ct_doc.py` | https://www.ctinmateinfo.state.ct.us/ | ✅ Live | `resultsupv.asp` & `detailsupv.asp`; A-Z last-name rotation; extracts inmate #, charges, facility, status, bond amount |
