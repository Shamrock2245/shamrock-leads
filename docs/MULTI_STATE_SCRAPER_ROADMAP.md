# Multi-State Scraper Expansion Roadmap

> Palmetto Surety licensed states: **FL, SC, NC, TN, TX, CT, LA, MS**  
> Plus **GA** (adjacent market / existing build).  
> Last updated: 2026-07-14  
> **Live registered (dashboard):** 51 FL · 74 GA · 46 SC · 27 NC = **198** — see root `STATUS.md`

## Why this order

1. **FL** — OSI home market + densest scrapers already live ✅  
2. **SC** — Palmetto HQ-adjacent; **46/46 registered** (production depth ongoing) ✅ registry  
3. **GA** — large existing Track A/B/C investment 🔄  
4. **NC** — wave-1 **27 registered**; deepen scrapes then expand 🔄  
5. **TN** — shared JMS vendors with SC/GA (Zuercher, Southern SW, JailTracker) 🔲  
6. **LA → MS** — Gulf corridor overlap with FL panhandle ops 🔲  
7. **TX** — largest county count; needs dedicated recon wave 🔲  
8. **CT** — only 8 counties; quick once bases are solid 🔲  

## Shared platform bases (leverage first)

| Base | File | States using today |
|------|------|--------------------|
| Zuercher | `scrapers/zuercher_base.py` | GA, SC |
| JailTracker | `scrapers/jailtracker_base.py` | FL, SC, GA |
| Southern Software | `scrapers/southern_sw_base.py` | GA, SC |
| P2C | `scrapers/p2c_base.py` | FL, GA, SC |
| SmartCOP | `scrapers/smartcop_base.py` | FL, GA, SC |
| New World | `scrapers/new_world_base.py` | FL, GA, SC |
| Kologik | `scrapers/kologik_base.py` | FL (Calhoun FL); reusable |
| Odyssey-style | `scrapers/odyssey_base.py` | GA stubs |
| EAS | `scrapers/eas_base.py` | GA batch |
| XML feed | `scrapers/xml_feed_base.py` | GA |

**Rule:** before writing a custom county scraper, check if the roster is one of the above platforms. Thin wrappers are preferred.

## Identity rules (non-negotiable)

- `scraper_id` includes state for non-FL: `scraper_sc_lee`, `scraper_ga_lee`  
- FL keeps legacy `scraper_lee` for dashboard compatibility  
- One-shot CLI: `python main.py sc_jasper`  
- Every `ArrestRecord.State` must match the scraper state  
- Never collapse multi-state counties with the same name into one job  

## Per-state playbook

### SC (registered — harden production)
1. **All 46 modules registered** — see `docs/SC_COUNTY_REGISTRY.md`  
2. Live custom paths: Beaufort XML, Charleston, York, Florence, Horry, Richland, Jasper, …  
3. **Greenville** — Incapsula; residential SOCKS when available  
4. Proxy path for 403 jailroster.org family; scaffold quiet no-portal counties  

### NC (wave-1 registered — first prod scrapes)
1. **NC recon ✅** + **27 wave-1 scrapers registered** — `docs/NC_COUNTY_REGISTRY.md`  
2. Platforms: Southern SW + Zuercher + classic P2C + Meck/Durham custom  
3. Dashboard Multi-State Ops filters NC; run-now uses `nc_*` keys  
4. Cloud P2C (Wake/Guilford/Forsyth) still needs residential WAF strategy  
5. TN recon pass next → `docs/TN_RECON_RESULTS.md`

### TX
1. Prioritize top-25 population counties first (Harris, Dallas, Tarrant, Bexar, Travis…)  
2. Expect heavy Odyssey/Tyler + custom municipal jails  

### CT / LA / MS
1. Recon → registry → wrappers  
2. CT can be completed in one session once patterns known  

## Directory layout

```
scrapers/
  counties/          # FL
  counties_ga/       # GA
  counties_sc/       # SC
  counties_nc/       # NC (scaffold)
  counties_tn/       # TN (scaffold)
  counties_tx/       # TX (scaffold)
  counties_ct/       # CT (scaffold)
  counties_la/       # LA (scaffold)
  counties_ms/       # MS (scaffold)
  *_base.py          # shared platforms
```

## Definition of done (per county)

- [ ] Roster URL + vendor documented in state registry  
- [ ] Scraper returns `ArrestRecord` with County, State, Booking_Number, Full_Name, Charges  
- [ ] Registered in `main.register_scrapers`  
- [ ] One-shot scrape returns ≥0 without exception (empty OK if documented)  
- [ ] `state` property set correctly  
- [ ] No PII in logs  
