# North Carolina County Registry

> Last updated: 2026-07-14  
> Goal: all **100** NC counties (Palmetto multi-state footprint)  
> Code: `scrapers/counties_nc/` (scaffold) · Recon: [`docs/NC_RECON_RESULTS.md`](./NC_RECON_RESULTS.md)

## Coverage Summary

| Status | Count | Notes |
|--------|------:|-------|
| ✅ Wave-1 registered | **27** | Southern SW, Zuercher, P2C classic, Davidson, Gaston, Meck/Durham scaffolds |
| Scheduler / dashboard | **27** | `scraper_nc_*` · `County (NC)` labels · Multi-State Ops filter |
| 🔲 Planned (portal mapped, not wave-1) | ~55 | URL + platform in recon |
| ⬜ No public web roster / app / VINE | ~45 | Skip or VINE-only until portal appears |
| First production scrapes | ⏳ | Run via dashboard Multi-State Ops or `python main.py nc_mecklenburg` |

**CLI one-shot (planned):** use `nc_` prefix to avoid FL/GA name collisions:

```bash
python main.py nc_mecklenburg
python main.py nc_wake
python main.py nc_lee      # not FL Lee
```

## Platform Map (planned wrappers)

| Platform | Counties (recon) | Base class (existing) |
|----------|------------------|------------------------|
| P2C / CentralSquare | Alamance, Alexander, Buncombe, Cabarrus, Cleveland, Forsyth, Guilford, Iredell, Lincoln, New Hanover, Robeson, Rowan, Union, Wake (+ Burke/Morganton PD?) | `P2CBaseScraper` |
| Southern Software | Anson, Duplin, Edgecombe, Harnett, Henderson, Polk, Sampson, Scotland, Stokes, Surry, Transylvania | `SouthernSWBaseScraper` |
| Zuercher | Brunswick, Davie, Hoke, Pender, Rutherford | `ZuercherBaseScraper` |
| New World | Gaston | `NewWorldBaseScraper` |
| DCN family | Halifax, Lee, Moore, Richmond, Sampson (legacy) | New thin base or per-file |
| Custom / OCV / PDF | Mecklenburg, Durham, Davidson, Randolph, Craven, Pitt, Carteret, Orange, Johnston, Caldwell, Chatham, Stanly, Cumberland, Catawba | per-file |
| JailTracker / SmartCOP | — | none confirmed |

## County Status

Status key: 🔲 Planned · ⬜ No public portal · ✅ Live · 🟡 Partial · ❌ Blocked

### A–C

| County | Status | Platform | Notes |
|--------|--------|----------|-------|
| Alamance | 🔲 Planned | P2C classic | `apps.alamance-nc.com/p2c/jailinmates.aspx` |
| Alexander | 🔲 Planned | P2C classic | `p2c.alexandercountync.gov` — DNS flaky |
| Alleghany | ⬜ No portal | — | VINE / phone |
| Anson | 🔲 Planned | Southern SW | AgencyID=`AnsonCoNC` |
| Ashe | ⬜ No portal | — | VINE / phone |
| Avery | ⬜ No portal | — | VINE / phone |
| Beaufort | ⬜ No portal | — | VINE / phone |
| Bertie | ⬜ No portal | Southern SW agency only | No confinemen link |
| Bladen | ⬜ No portal | — | VINE / phone |
| Brunswick | 🔲 Planned | Zuercher | Official link; DNS fail from some hosts |
| Buncombe | 🔲 Planned | P2C cloud | WAF from datacenter |
| Burke | 🔲 Planned | P2C cloud (Morganton PD?) | **Validate agency scope** |
| Cabarrus | 🔲 Planned | P2C classic | High conf 200 |
| Caldwell | 🔲 Planned | PDF roster | DocumentCenter PDF |
| Camden | ⬜ No portal | — | VINE / phone |
| Carteret | 🔲 Planned | Custom | `inmateinfo.carteretcountync.gov` |
| Caswell | ⬜ No portal | — | VINE / phone |
| Catawba | 🔲 Planned | Custom | `injail.catawbacountync.gov/whosinjail/` |
| Chatham | 🔲 Planned | OCV web | `chathamsheriff.com/inmateSearch` |
| Cherokee | 🔲 Planned | DCN? | Port 8080 DCN URL historically; unreachable 2026-07-14 |
| Chowan | ⬜ No portal | — | VINE / phone |
| Clay | ⬜ No portal | — | VINE / phone |
| Cleveland | 🔲 Planned | P2C classic | IP host `74.218.167.200` — fragile |
| Columbus | ⬜ App-only | OCV app | No open web roster |
| Craven | 🔲 Planned | Custom GIS | `gis.cravencountync.gov/images/activebookings` |
| Cumberland | 🔲 Planned | Custom | Active inmate page — host flaky |
| Currituck | ⬜ No portal | — | VINE / phone |

### D–H

| County | Status | Platform | Notes |
|--------|--------|----------|-------|
| Dare | ⬜ No portal | — | VINE / phone |
| Davidson | 🔲 Planned | Custom HTML | `www2.co.davidson.nc.us/DCInmates/` |
| Davie | 🔲 Planned | Zuercher | `davie-so-nc.zuercherportal.com` |
| Duplin | 🔲 Planned | Southern SW | AgencyID=`DuplinCoNC` |
| Durham | 🔲 Planned | Custom IPS | `www2.dconc.gov/sheriff/ips/default.aspx` |
| Edgecombe | 🔲 Planned | Southern SW | AgencyID=`EdgecombeCoNC` |
| Forsyth | 🔲 Planned | P2C cloud | WAF |
| Franklin | ⬜ No portal | — | VINE / phone |
| Gaston | 🔲 Planned | New World | `tepsweb.cityofgastonia.com/NewWorld.InmateInquiry/GastonCounty` |
| Gates | ⬜ No portal | — | VINE / phone |
| Graham | ⬜ No portal | — | VINE / phone |
| Granville | ⬜ No portal | — | VINE / phone |
| Greene | ⬜ No portal | — | VINE / phone |
| Guilford | 🔲 Planned | P2C cloud | WAF; dual jails |
| Halifax | 🔲 Planned | DCN | `inmates.halifaxncsheriff.com/dcn/` |
| Harnett | 🔲 Planned | Southern SW | AgencyID=`HarnettCoNC` |
| Haywood | ⬜ No portal | — | VINE / phone |
| Henderson | 🔲 Planned | Southern SW | AgencyID=`HendersonCoNC` |
| Hertford | ⬜ No portal | — | VINE / phone |
| Hoke | 🔲 Planned | Zuercher | `hoke-so-nc.zuercherportal.com` |
| Hyde | ⬜ No portal | — | VINE / phone |

### I–O

| County | Status | Platform | Notes |
|--------|--------|----------|-------|
| Iredell | 🔲 Planned | P2C classic | `p2c.iredellcountync.gov` |
| Jackson | ⬜ App-only | OCV app | App-centric |
| Johnston | 🔲 Planned | Custom | `jcso.org/detention-center/inmate-search/` |
| Jones | ⬜ No portal | — | VINE / phone |
| Lee | 🔲 Planned | DCN | `dcn.leecountync.gov/dcn/inmates` |
| Lenoir | ⬜ No portal | — | VINE / phone |
| Lincoln | 🔲 Planned | P2C classic | `p2c.lincolnsheriff.org` |
| Macon | ⬜ No portal | — | VINE / phone |
| Madison | ⬜ No portal | — | VINE / phone |
| Martin | ⬜ No portal | — | VINE / phone |
| McDowell | ⬜ No portal | — | VINE / phone |
| **Mecklenburg** | 🔲 Planned | Custom MCSO | Top build target |
| Mitchell | ⬜ No portal | — | VINE / phone |
| Montgomery | ⬜ No portal | — | VINE / phone |
| Moore | 🔲 Planned | DCN | `webapps.moorecountync.gov/dcn/inmates` |
| Nash | ⬜ No portal | — | VINE / phone |
| New Hanover | 🔲 Planned | P2C classic | `p2c.nhcgov.com/p2c/jailinmates.aspx` |
| Northampton | ⬜ No portal | — | VINE / phone |
| Onslow | ⬜ App-only | OCV app | No stable web roster |
| Orange | 🔲 Planned | Custom React | `ocsonc.com/detention/current-detainees` |

### P–Z

| County | Status | Platform | Notes |
|--------|--------|----------|-------|
| Pamlico | ⬜ No portal | — | VINE / phone |
| Pasquotank | ⬜ No portal | — | VINE / phone |
| Pender | 🔲 Planned | Zuercher | `pender-so-nc.zuercherportal.com` |
| Perquimans | ⬜ No portal | — | VINE / phone |
| Person | ⬜ No portal | — | VINE / phone |
| Pitt | 🔲 Planned | Custom app | `apps.pittcountync.gov/apps/detention/detainee/` |
| Polk | 🔲 Planned | Southern SW | AgencyID=`PolkCoNC` |
| Randolph | 🔲 Planned | Custom ASP.NET | legacyweb ConfinedInmates* |
| Richmond | 🔲 Planned | DCN | `webapp01.richmondnc.com/dcn/` |
| Robeson | 🔲 Planned | P2C cloud | WAF |
| Rockingham | ⬜ No portal | — | VINE / phone |
| Rowan | 🔲 Planned | P2C cloud | WAF |
| Rutherford | 🔲 Planned | Zuercher | `rutherford-so-nc.zuercherportal.com` |
| Sampson | 🔲 Planned | Southern SW | Prefer SW over legacy DCN IP |
| Scotland | 🔲 Planned | Southern SW | AgencyID=`ScotlandCoNC` |
| Stanly | 🔲 Planned | OCV web | `stanlysheriff.us/inmateList` |
| Stokes | 🔲 Planned | Southern SW | AgencyID=`StokesCoNC` |
| Surry | 🔲 Planned | Southern SW | AgencyID=`SurryCoNC` |
| Swain | ⬜ No portal | — | VINE / phone |
| Transylvania | 🔲 Planned | Southern SW | AgencyID=`TransylvaniaCoNC` |
| Tyrrell | ⬜ No portal | — | VINE / phone |
| Union | 🔲 Planned | P2C classic | High conf 200 |
| Vance | ⬜ No portal | — | VINE / phone |
| **Wake** | 🔲 Planned | P2C cloud | Top build; WAF |
| Warren | ⬜ No portal | — | VINE / phone |
| Washington | ⬜ No portal | — | VINE / phone |
| Watauga | ⬜ No portal | — | VINE / phone |
| Wayne | ⬜ Unverified | — | CivicPlus CTA only |
| Wilkes | ⬜ No portal | — | VINE / phone |
| Wilson | ⬜ App-only | OCV app | — |
| Yadkin | ⬜ No portal | — | VINE / phone |
| Yancey | ⬜ No portal | — | VINE / phone |

## Next build priorities

See **Top 10** and wave plan in [`NC_RECON_RESULTS.md`](./NC_RECON_RESULTS.md).

1. Mecklenburg custom  
2. Wake / Guilford / Forsyth P2C (proxy)  
3. Gaston New World  
4. Southern SW NC batch (11)  
5. Zuercher NC batch (4–5)  
6. Classic P2C 200s (Cabarrus, Union, NHC, Alamance, Iredell, Lincoln)

## Multi-state roadmap (Palmetto)

| State | Counties (approx) | Code dir | Status |
|-------|------------------:|----------|--------|
| FL | 67 | `scrapers/counties/` | Primary |
| GA | 159 | `scrapers/counties_ga/` | Expanding |
| SC | 46 | `scrapers/counties_sc/` | Building |
| **NC** | **100** | **`scrapers/counties_nc/`** | **Recon complete · build pending** |
| TN | 95 | `scrapers/counties_tn/` | Scaffold |
| TX | 254 | `scrapers/counties_tx/` | Scaffold |
| CT | 8 | `scrapers/counties_ct/` | Scaffold |
| LA | 64 | `scrapers/counties_la/` | Scaffold |
