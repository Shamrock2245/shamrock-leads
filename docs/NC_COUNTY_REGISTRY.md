# North Carolina County Registry

> Last updated: 2026-08-04  
> Goal: all **100** NC counties (Palmetto multi-state footprint)  
> Code: `scrapers/counties_nc/` · Recon: [`docs/NC_RECON_RESULTS.md`](./NC_RECON_RESULTS.md)

## Coverage Summary

| Status | Count | Notes |
|--------|------:|-------|
| ✅ Registered | **47** | Waves 1–7: + Chatham/Stanly OCV · Orange daily PDF |
| Scheduler / dashboard | **47** | `scraper_nc_*` · `County (NC)` labels · Multi-State Ops filter |
| 🔲 Planned (portal mapped, not built) | ~40 | URL + platform in recon |
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
| Buncombe | ✅ Live | Police-to-Citizen SPA | `buncombecountyso.policetocitizen.com` · browser letter walk |
| Burke | 🔲 Planned | P2C cloud (Morganton PD?) | **Validate agency scope** |
| Cabarrus | 🔲 Planned | P2C classic | High conf 200 |
| Caldwell | ✅ Live | Daily PDF | DocumentCenter in-custody PDF · ~150 |
| Camden | ⬜ No portal | — | VINE / phone |
| Carteret | ✅ Live | DCN | `inmateinfo.carteretcountync.gov/inmates` · `dcn_base` |
| Caswell | ⬜ No portal | — | VINE / phone |
| Catawba | ✅ Live | Custom HTML | Who's In Jail table · ~410 + bond/charges |
| Chatham | ✅ Live | OCV JSON | `myocv.s3…/a104027312/inmates.json` · ~94 |
| Cherokee | 🔲 Planned | DCN? | Port 8080 DCN URL historically; unreachable 2026-07-14 |
| Chowan | ⬜ No portal | — | VINE / phone |
| Clay | ⬜ No portal | — | VINE / phone |
| Cleveland | 🔲 Planned | P2C classic | IP host `74.218.167.200` — fragile |
| Columbus | ⬜ App-only | OCV app | No open web roster |
| Craven | ✅ Live | ArcGIS MapServer | `BookingsPublic/MapServer/0` · ~336 inmates + bond/statutes |
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
| Halifax | ✅ Live | DCN | `inmates.halifaxncsheriff.com/dcn/inmates` · shared `dcn_base` (list ≤100 + detail enrich) |
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
| Johnston | ✅ Live | ColdFusion | `johnstonnc.com/sheriffs_office/b_jailsearch2s.cfm` · ~296 |
| Jones | ⬜ No portal | — | VINE / phone |
| Lee | ✅ Live | DCN | `dcn.leecountync.gov/dcn/inmates` · `dcn_base` (CLI `nc_lee`) |
| Lenoir | ⬜ No portal | — | VINE / phone |
| Lincoln | 🔲 Planned | P2C classic | `p2c.lincolnsheriff.org` |
| Macon | ⬜ No portal | — | VINE / phone |
| Madison | ⬜ No portal | — | VINE / phone |
| Martin | ⬜ No portal | — | VINE / phone |
| McDowell | ⬜ No portal | — | VINE / phone |
| **Mecklenburg** | 🔲 Planned | Custom MCSO | Top build target |
| Mitchell | ⬜ No portal | — | VINE / phone |
| Montgomery | ⬜ No portal | — | VINE / phone |
| Moore | ✅ Live | DCN | `webapps.moorecountync.gov/dcn/inmates` · `dcn_base` |
| Nash | ⬜ No portal | — | VINE / phone |
| New Hanover | 🔲 Planned | P2C classic | `p2c.nhcgov.com/p2c/jailinmates.aspx` |
| Northampton | ⬜ No portal | — | VINE / phone |
| Onslow | ⚠️ Degraded | P2C + FingerprintJS | `p2c.ocsheriff.com` often sinkhole/timeout; fail closed · app still primary for public |
| Orange | ✅ Live | Daily PDF | Wix portal → newest ugd PDF · ~50 + bonds |

### P–Z

| County | Status | Platform | Notes |
|--------|--------|----------|-------|
| Pamlico | ⬜ No portal | — | VINE / phone |
| Pasquotank | ⬜ No portal | — | VINE / phone |
| Pender | 🔲 Planned | Zuercher | `pender-so-nc.zuercherportal.com` |
| Perquimans | ⬜ No portal | — | VINE / phone |
| Person | ⬜ No portal | — | VINE / phone |
| Pitt | ✅ Live | Custom ASP.NET | Letter-walk detainee search · ~300 active · booking #s |
| Polk | 🔲 Planned | Southern SW | AgencyID=`PolkCoNC` |
| Randolph | ✅ Live | ASP.NET HTML | ConfinedInmatesByName · ~362 with charges/bail |
| Richmond | ✅ Live | DCN | `webapp01.richmondnc.com/dcn/inmates` · `dcn_base` |
| Robeson | 🔲 Planned | P2C cloud | WAF |
| Rockingham | ⬜ No portal | — | VINE / phone |
| Rowan | 🔲 Planned | P2C cloud | WAF |
| Rutherford | 🔲 Planned | Zuercher | `rutherford-so-nc.zuercherportal.com` |
| Sampson | 🔲 Planned | Southern SW | Prefer SW over legacy DCN IP |
| Scotland | 🔲 Planned | Southern SW | AgencyID=`ScotlandCoNC` |
| Stanly | ✅ Live | OCV JSON | `myocv.s3…/a109928001/inmates.json` · ~143 |
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
**As of 2026-08-04:** **47 registered** (waves 1–7 including Pitt, DCN cluster, Craven, Randolph, Catawba, Carteret, Caldwell, Chatham/Stanly OCV, Orange PDF).

1. Harden registered metros (Meck, Wake WAF, Guilford, Forsyth) — residential proxy  
2. DCN pagination beyond first 100 (DevExpress AJAX unreliable from DC IPs)  
3. More OCV `app_id` counties (Wilson, etc.) where S3 inmates.json is public  
4. Rowan / Robeson cloud P2C (proxy)  
5. Alexander classic P2C (DNS flaky)  
6. Remaining rural / no-portal counties (VINE-only — skip or low priority)

## Multi-state roadmap (Palmetto)

| State | Goal | Registered | Code dir | Status |
|-------|-----:|----------:|----------|--------|
| FL | 67 | **67** | `scrapers/counties/` | Primary complete |
| GA | 159 | **74** | `scrapers/counties_ga/` | Expanding |
| SC | 46 | **46** | `scrapers/counties_sc/` | Registered · depth ongoing |
| **NC** | **100** | **47** | **`scrapers/counties_nc/`** | **Waves 1–7 · expand portals** |
| TN | 95 | **9** | `scrapers/counties_tn/` | Expanding |
| TX | 254 | **15** | `scrapers/counties_tx/` | Expanding |
| CT | statewide | **2** | `scrapers/counties_ct/` | DOC + dockets hardened |
| LA | 64 | **4** | `scrapers/counties_la/` | Expanding |
| AL | — | **3** | `scrapers/counties_al/` | Major metros |
| MS | — | **2** | `scrapers/counties_ms/` | Major metros |
| **Total** | — | **269** | `REGISTERED_COUNTIES` | See `STATUS.md` |
