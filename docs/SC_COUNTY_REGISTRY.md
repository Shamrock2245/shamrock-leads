# South Carolina County Registry

> Last updated: 2026-07-14  
> Goal: all **46** SC counties (Palmetto surety footprint)  
> Code: `scrapers/counties_sc/` · Recon: `docs/SC_RECON_RESULTS.md`

## Coverage Summary

| Status | Count | Notes |
|--------|------:|-------|
| Registered in scheduler / dashboard | **46** (all SC counties) | `scraper_sc_*` job IDs · `County (SC)` labels |
| Production HTML/XML verified | 5+ | Beaufort (XML), Jasper (WP cards), Charleston, York, Florence, Horry, Richland |
| Platform thin wrappers | 16+ | Zuercher, JailTracker, Southern SW, P2C, SmartCOP, New World |
| Scaffold / blocked | rest | No public portal, CAPTCHA, Cloudflare, or bad recon URL |
| Missing module entirely | **0** | All 46 files present under `scrapers/counties_sc/` |

**CLI one-shot:** use state prefix to avoid FL/GA name collisions:

```bash
python main.py sc_jasper
python main.py sc_charleston
python main.py sc_lee      # not FL Lee
```

## Platform Map (built)

| Platform | Counties | Base class |
|----------|----------|------------|
| Zuercher | Anderson, Cherokee, Colleton, Kershaw, Laurens, Oconee, Pickens, Union | `ZuercherBaseScraper` |
| JailTracker | Chester, Greenwood | `JailTrackerBaseScraper` |
| Southern Software | Chesterfield, Dorchester | `SouthernSWBaseScraper` |
| P2C / CentralSquare | Lexington, Lee | `P2CBaseScraper` |
| SmartCOP | Sumter | `SmartCOPBaseScraper` |
| New World | Lancaster | `NewWorldBaseScraper` |
| Custom / XML | Beaufort, Charleston, Florence, Horry, York, Jasper, Aiken… | per-file |

## County Status

| County | Status | Platform | Notes |
|--------|--------|----------|-------|
| Aiken | 🟡 Partial | Custom iframe | TLS fails from some hosts to lookups.aikencountysc.gov |
| Anderson | 🟡 Wrapper | Zuercher | `anderson-so-sc.zuercherportal.com` |
| Bamberg | 🟡 Stub | Custom | 403 from datacenter |
| Beaufort | ✅ Live | XML feed | `mugshots.bcgov.net/jailrostera.xml` |
| Berkeley | 🟡 Stub | Custom | Needs parser |
| Calhoun | ❌ Blocked | — | Prior Kologik URL is **Calhoun FL** (FL0070000 / Blountstown) |
| Charleston | ✅ Built | ASP.NET | 7-day booking search |
| Cherokee | 🟡 Wrapper | Zuercher | |
| Chester | 🟡 Wrapper | JailTracker | CAPTCHA path |
| Chesterfield | 🟡 Wrapper | Southern SW | |
| Colleton | 🟡 Wrapper | Zuercher | |
| Darlington | 🟡 Stub | Custom | |
| Dorchester | 🟡 Wrapper | Southern SW | |
| Florence | ✅ Live | DevExpress ASP.NET | Letter walk on booking.fcso.org; name/age/race/sex/booked |
| Georgetown | 🟡 Scaffold | — | No machine-readable roster |
| Greenville | ❌ Blocked | Custom + Incapsula | Official `app.greenvillecounty.org/inmate_search.htm`. Datacenter 403; needs `GREENVILLE_SOCKS_PROXY` / residential. Scraper ready when proxy available. |
| Greenwood | 🟡 Wrapper | JailTracker | |
| Hampton | 🟡 Stub | Custom | 403 |
| Horry | ✅ Built | Custom / JSON | |
| Jasper | ✅ Live | WP cards | Verified 42 inmates (2026-07-14) |
| Kershaw | 🟡 Wrapper | Zuercher | |
| Lancaster | 🟡 Wrapper | New World | |
| Laurens | 🟡 Wrapper | Zuercher | |
| Lee | 🟡 Wrapper | P2C | URL may be wrong agency — re-verify |
| Lexington | 🟡 Wrapper | P2C | |
| Marion | 🟡 Stub | Custom | 403 |
| Marlboro | 🟡 Scaffold | Custom | Cloudflare/403 |
| Newberry | 🟡 Stub | Custom | |
| Oconee | 🟡 Wrapper | Zuercher | |
| Pickens | 🟡 Wrapper | Zuercher | |
| Richland | ✅ Live | ASP.NET JMSOnline | Captcha = `hidStrRandom` token. Digraph last-name walk (A–Z + digraphs when paged). List view: name/age/ht/wt/booked (no charges on list). |
| Spartanburg | 🟡 Scaffold | — | Prior 72h URL 404 |
| Sumter | 🟡 Wrapper | SmartCOP | |
| Union | 🟡 Wrapper | Zuercher | |
| York | ✅ Built | ASP.NET | |

### Not yet scaffolded (typically no public portal)

Abbeville, Allendale, Barnwell, Clarendon, Dillon, Edgefield, Fairfield, McCormick, Orangeburg, Saluda, Williamsburg

## Next build priorities

1. **Greenville** — enable residential SOCKS (`GREENVILLE_SOCKS_PROXY`); scraper code ready
2. **Richland** — optional charge/bond detail enrichment (list view is live)
3. **Zuercher API hardening** — confirm SC portals return JSON; add DrissionPage fallback
4. **JailTracker SC** — Chester/Greenwood with existing CAPTCHA cascade
5. **Bamberg/Hampton family** — shared jailroster theme sites via residential proxy
6. **NC wave 1** — Southern SW + Zuercher + classic P2C (`docs/NC_RECON_RESULTS.md`)

## Multi-state roadmap (Palmetto)

| State | Counties (approx) | Code dir | Status |
|-------|------------------:|----------|--------|
| FL | 67 | `scrapers/counties/` | Primary — ~49 registered |
| GA | 159 | `scrapers/counties_ga/` | Expanding — 74 registered + EAS batch |
| SC | 46 | `scrapers/counties_sc/` | Building — 35 registered (Richland live) |
| NC | 100 | `scrapers/counties_nc/` | Recon complete — see NC_RECON_RESULTS.md |
| TN | 95 | `scrapers/counties_tn/` | Scaffold |
| TX | 254 | `scrapers/counties_tx/` | Scaffold |
| CT | 8 | `scrapers/counties_ct/` | Scaffold |
| LA | 64 | `scrapers/counties_la/` | Scaffold |
| MS | 82 | `scrapers/counties_ms/` | Scaffold |
