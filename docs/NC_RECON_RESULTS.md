# North Carolina Recon Results

> Generated: 2026-07-14  
> Scope: NC county **jail / detention** inmate search portals (not NCDAC state prisons)  
> Method: official sheriff pages, Southern Software Citizen Connect directory, Zuercher subdomain probes, P2C/New World/DCN pattern search, HTTP GET verification from recon host  
> **Do not treat aggregators (ncinmatesearch.org, jail roster SEO sites) as source of truth** — used only as lead generators; official URLs re-verified where possible.

---

## Summary table (counts by platform)

| Platform | Counties mapped | Confidence notes |
|----------|----------------:|------------------|
| **P2C / CentralSquare** (classic `jailinmates.aspx` + `*.policetocitizen.com`) | **18** | Largest share of metro NC; cloud P2C often WAF-blocks datacenter IPs (“Request Rejected”) |
| **Southern Software** Citizen Connect (`cc.southernsoftware.com/bookingsearch`) | **11** | Confirmed confinemen AgencyIDs; High confidence |
| **DCN** (Detention Center Network / DevExpress-style rosters) | **5+** | Lee, Moore, Halifax, Sampson (legacy IP), Richmond, Transylvania (alt), Cherokee (stale?) |
| **Zuercher Portal** (`*-so-nc.zuercherportal.com`) | **5** | 4 live from recon host + Brunswick linked officially (DNS fail from host → Medium) |
| **Custom HTML / ASP.NET / GIS** | **10+** | Mecklenburg, Durham, Davidson, Randolph, Craven, Pitt, Carteret, Orange, Johnston, Caldwell PDF |
| **New World / Tyler** | **1** | Gaston (cityofgastonia NewWorld.InmateInquiry) |
| **OCV / sheriff mobile-web** | **3+** | Chatham, Stanly; many mid-market apps (Onslow, Wilson, Columbus, Henderson) |
| **JailTracker** | **0** | No NC public JailTracker OMS IDs confirmed |
| **SmartCOP** | **0** | None confirmed |
| **VINE / app-only / no public web roster** | **~40–50** | Remainder of 100 counties; statewide VINE: https://vinelink.vineapps.com/state/NC |

**Priority recon coverage:** all **50** named priority counties addressed below (URL and/or explicit no-portal / app-only).  
**Additional counties** with confirmed portals also listed in platform clusters.

### HTTP verification legend (2026-07-14 recon host)

| Code | Meaning |
|------|---------|
| **200** | GET succeeded from recon host |
| **WAF** | 200 body = “Request Rejected” (portal exists; scrape needs residential egress) |
| **DNS/TLS** | URL documented by sheriff but failed from recon host (still list as Medium) |
| **403** | Forbidden from recon host |

---

## Tier 1 Metro (detailed)

Population-first metros / large suburbs. **Build scrapers here first.**

| County | Seat / market | URL | Platform | Confidence | Notes |
|--------|---------------|-----|----------|------------|-------|
| **Mecklenburg** | Charlotte | https://mecksheriffweb.mecklenburgcountync.gov/Inmate | Custom ASP.NET (MCSO) | **High** | Also Arrest Inquiry `/Arrest`. **200 OK**. Largest NC jail. |
| **Wake** | Raleigh | https://wakeso.policetocitizen.com/Inmates | P2C cloud | **High** | Catalog path `/Inmates/Catalog`. **WAF** from datacenter. |
| **Guilford** | Greensboro / High Point | https://guilfordcountysheriff.policetocitizen.com/Inmates | P2C cloud | **High** | Linked from guilfordcountync.gov. **WAF**. Dual facilities. |
| **Forsyth** | Winston-Salem | https://forsythsheriffnc.policetocitizen.com/Inmates | P2C cloud | **High** | Linked from forsyth.cc LEDC page. **WAF**. |
| **Cumberland** | Fayetteville | https://cumberlandsheriffnc.gov/active-inmate-page/ | Custom (ASP.NET legacy also `ccsonc.org:8000/...`) | **Medium** | Official page + historic `:8000/active_inmates/Inmates.aspx`. **DNS/TLS fail** from recon host — re-check from residential IP. |
| **Durham** | Durham | https://www2.dconc.gov/sheriff/ips/default.aspx | Custom ASP.NET “Inmate Population Search” | **High** | Mirror: `http://www2.durhamcountync.gov/sheriff/ips/...` (HTTP). **200 OK**. Links out to VINE for notifications. |
| **Buncombe** | Asheville | https://buncombecountyso.policetocitizen.com/Inmates | P2C cloud | **High** | Linked from buncombesheriff.com. **WAF**. |
| **New Hanover** | Wilmington | https://p2c.nhcgov.com/p2c/jailinmates.aspx | P2C classic | **High** | **200 OK** with Inmate Inquiry UI. |
| **Gaston** | Gastonia | https://tepsweb.cityofgastonia.com/NewWorld.InmateInquiry/GastonCounty | New World | **High** | **200 OK**. Alt path: `/newworld.aegis.webportal/Corrections/InmateInquiry.aspx`. |
| **Union** | Monroe | https://sheriff.unioncountync.gov/jailinmates.aspx | P2C classic | **High** | **200 OK**. Linked from unioncountysheriffsoffice.com. |
| **Cabarrus** | Concord | https://onlineservices.cabarruscounty.us/p2c/jailinmates.aspx | P2C classic | **High** | **200 OK**. Official “Find an Inmate” CTA. |
| **Johnston** | Smithfield | https://jcso.org/detention-center/inmate-search/ | Custom (JS-heavy) | **Medium** | **200 OK** shell; may need browser. Older CFM: `johnstonnc.com/sheriffs_office/b_jailsearch1.cfm` (**403** from host). |
| **Onslow** | Jacksonville | App-centric (OCSO mobile app) | OCV app / VINE | **Medium** | Sheriff FB/app promote inmate search; **no stable public web roster** found on onslowcountync.gov. |
| **Pitt** | Greenville | https://apps.pittcountync.gov/apps/detention/detainee/ | Custom web app | **High** | **200 OK** “Detainee Search”. Linked from pittcountync.gov directory. |
| **Catawba** | Newton | https://injail.catawbacountync.gov/whosinjail/ | Custom “Who’s in Jail” | **Medium** | Documented widely; **TLS timeout** from recon host — verify from residential IP. |
| **Iredell** | Statesville | https://p2c.iredellcountync.gov/jailinmates.aspx | P2C classic | **High** | **200 OK** Inmate Inquiry UI. CivicPlus page also links statewide VINE. |
| **Davidson** | Lexington | http://www2.co.davidson.nc.us/DCInmates/ | Custom HTML list | **High** | **200 OK** (HTTP only). Linked from davidsoncountysheriffsnc.org. |
| **Alamance** | Graham | https://apps.alamance-nc.com/p2c/jailinmates.aspx | P2C classic (OSSI-branded) | **High** | **200 OK**. |
| **Rowan** | Salisbury | https://rowancountync.policetocitizen.com/Inmates | P2C cloud | **High** | **WAF**. |
| **Robeson** | Lumberton | https://robesoncoso.policetocitizen.com/Inmates | P2C cloud | **High** | **WAF**. Aggregators sometimes omit — portal is real. |

---

## Tier 2 Mid-market

| County | URL | Platform | Confidence | Notes |
|--------|-----|----------|------------|-------|
| **Orange** | https://www.ocsonc.com/detention/current-detainees | Custom (React / Wix-family) | **High** | **200 OK**. PDF download option on page. |
| **Brunswick** | https://brunswick-so-nc.zuercherportal.com/#/inmates | Zuercher | **Medium** | Official brunswicksheriff.com “I AGREE” link; **DNS fail** from recon host. Landing: https://www.brunswicksheriff.com/detention-center/inmate-search |
| **Randolph** | https://legacyweb.randolphcountync.gov/sheriff/ConfinedInmatesByName.aspx | Custom ASP.NET | **High** | Also by-date twin URL. Hub: https://www.randolphcountync.gov/369/Confined-Inmates. Legacy host flaky from some networks. |
| **Wayne** | Phone / VINE / civicplus “Inmate Locator” CTA | Unclear / likely no open roster | **Low** | waynegov.com detention pages reference Inmate Locator; public machine-readable roster not verified. |
| **Harnett** | https://cc.southernsoftware.com/bookingsearch/index.php?AgencyID=HarnettCoNC | Southern Software | **High** | **200 OK**. PDF alternate sometimes posted on harnettsheriff.com. |
| **Craven** | https://gis.cravencountync.gov/images/activebookings | Custom GIS HTML | **High** | **200 OK**. Linked from cravencountync.gov Detention Center. |
| **Wilson** | Wilson County Sheriff OCV **mobile app** | OCV app | **Medium** | App advertises inmate search; web P2C at wilsonnc.policetocitizen.com is PD-focused (events/wanted), not full jail catalog. |
| **Caldwell** | https://www.caldwellcountync.org/DocumentCenter/View/1696/List-of-Current-Inmates-PDF | PDF roster | **High** | **200 OK** PDF. Hub: `/280/Detention`. |
| **Burke** | https://morgantonpdnc.policetocitizen.com/Inmates | P2C cloud (**city PD**) | **Low–Medium** | May be **Morganton PD** hold roster, not full county jail — validate before scrape. **WAF**. |
| **Cleveland** | http://74.218.167.200/p2c/jailinmates.aspx | P2C classic (IP host) | **Medium** | **200 OK** Inmate Inquiry. Prefer discovering hostname; IP portals are fragile. |
| **Surry** | https://cc.southernsoftware.com/bookingsearch/index.php?AgencyID=SurryCoNC | Southern Software | **High** | **200 OK**. |
| **Moore** | https://webapps.moorecountync.gov/dcn/inmates | DCN | **High** | **200 OK**. Root: `/dcn/`. |
| **Rockingham** | No public roster verified | VINE / phone | **Low** | Detention info page only; treat as no portal until proven. |
| **Carteret** | https://inmateinfo.carteretcountync.gov/ | Custom inmate info app | **Medium** | Linked from carteretcountync.gov “I AGREE”; **DNS/TLS fail** from recon host. |
| **Henderson** | https://cc.southernsoftware.com/bookingsearch/index.php?AgencyID=HendersonCoNC | Southern Software | **High** | **200 OK**. Also sheriff mobile app. |
| **Lincoln** | http://p2c.lincolnsheriff.org/jailinmates.aspx | P2C classic | **High** | **200 OK**. Alt IP observed historically: `35.131.165.188`. |
| **Wilkes** | No public roster verified | VINE / phone | **Low** | — |
| **Rutherford** | https://rutherford-so-nc.zuercherportal.com/#/inmates | Zuercher | **High** | **200 OK** Zuercher shell. |
| **Nash** | No public roster verified | VINE / phone | **Low** | — |
| **Stokes** | https://cc.southernsoftware.com/bookingsearch/index.php?AgencyID=StokesCoNC | Southern Software | **High** | **200 OK**. |
| **Sampson** | https://cc.southernsoftware.com/bookingsearch/index.php?AgencyID=SampsonCoNC | Southern Software | **High** | **200 OK**. Legacy DCN also at `http://66.207.228.197/dcn/` (**200**). Prefer SW. |
| **Stanly** | https://www.stanlysheriff.us/inmateList | OCV web | **High** | **200 OK**. myocv fingerprint. |
| **Lee** | https://dcn.leecountync.gov/dcn/inmates | DCN | **High** | **200 OK**. |
| **Chatham** | https://www.chathamsheriff.com/inmateSearch | OCV web | **High** | **200 OK**. VINE notify links on cards. |
| **Lenoir** | No public roster verified | VINE / phone | **Low** | — |
| **Edgecombe** | https://cc.southernsoftware.com/bookingsearch/index.php?AgencyID=EdgecombeCoNC | Southern Software | **High** | **200 OK**. |
| **Halifax** | https://inmates.halifaxncsheriff.com/dcn/ | DCN | **High** | **200 OK**. |
| **Columbus** | Columbus County Sheriff **OCV app** | OCV app | **Medium** | App markets inmate search; no open web roster confirmed. |
| **Duplin** | https://cc.southernsoftware.com/bookingsearch/index.php?AgencyID=DuplinCoNC | Southern Software | **High** | **200 OK**. |
| **Granville** | No public roster verified | VINE / phone | **Low** | — |

---

## Platform clusters

### P2C / CentralSquare (classic + cloud)

| County | URL | Flavor | HTTP |
|--------|-----|--------|------|
| Alamance | https://apps.alamance-nc.com/p2c/jailinmates.aspx | Classic | 200 |
| Alexander | https://p2c.alexandercountync.gov/jailinmates.aspx | Classic | DNS fail (Medium) |
| Buncombe | https://buncombecountyso.policetocitizen.com/Inmates | Cloud | WAF |
| Cabarrus | https://onlineservices.cabarruscounty.us/p2c/jailinmates.aspx | Classic | 200 |
| Cleveland | http://74.218.167.200/p2c/jailinmates.aspx | Classic IP | 200 |
| Forsyth | https://forsythsheriffnc.policetocitizen.com/Inmates | Cloud | WAF |
| Guilford | https://guilfordcountysheriff.policetocitizen.com/Inmates | Cloud | WAF |
| Iredell | https://p2c.iredellcountync.gov/jailinmates.aspx | Classic | 200 |
| Lincoln | http://p2c.lincolnsheriff.org/jailinmates.aspx | Classic | 200 |
| New Hanover | https://p2c.nhcgov.com/p2c/jailinmates.aspx | Classic | 200 |
| Robeson | https://robesoncoso.policetocitizen.com/Inmates | Cloud | WAF |
| Rowan | https://rowancountync.policetocitizen.com/Inmates | Cloud | WAF |
| Union | https://sheriff.unioncountync.gov/jailinmates.aspx | Classic | 200 |
| Wake | https://wakeso.policetocitizen.com/Inmates | Cloud | WAF |
| Burke (Morganton PD?) | https://morgantonpdnc.policetocitizen.com/Inmates | Cloud | WAF — **validate agency** |

**Scraper note:** Reuse `P2CBaseScraper` / SC patterns. Cloud P2C needs residential proxy or browser cascade (same as GA/SC experience).

---

### Southern Software Citizen Connect

Base booking search:  
`https://cc.southernsoftware.com/bookingsearch/index.php?AgencyID={ID}`

| County | AgencyID | HTTP |
|--------|----------|------|
| Anson | `AnsonCoNC` | 200 |
| Duplin | `DuplinCoNC` | 200 |
| Edgecombe | `EdgecombeCoNC` | 200 |
| Harnett | `HarnettCoNC` | 200 |
| Henderson | `HendersonCoNC` | 200 |
| Polk | `PolkCoNC` | 200 |
| Sampson | `SampsonCoNC` | 200 |
| Scotland | `ScotlandCoNC` | 200 |
| Stokes | `StokesCoNC` | 200 |
| Surry | `SurryCoNC` | 200 |
| Transylvania | `TransylvaniaCoNC` | 200 |

**Also on Citizen Connect (agency only / no confinemen link on index):** BertieCoNC, plus multiple NC **PDs** (Aberdeen, Southern Pines, etc.) — PD sites are **not** county jail rosters.

**Scraper note:** Reuse `SouthernSWBaseScraper` from SC/FL work.

---

### Zuercher Portal

| County | URL | HTTP |
|--------|-----|------|
| Davie | https://davie-so-nc.zuercherportal.com/#/inmates | 200 |
| Hoke | https://hoke-so-nc.zuercherportal.com/#/inmates | 200 |
| Pender | https://pender-so-nc.zuercherportal.com/#/inmates | 200 |
| Rutherford | https://rutherford-so-nc.zuercherportal.com/#/inmates | 200 |
| Brunswick | https://brunswick-so-nc.zuercherportal.com/#/inmates | DNS fail (Medium; official link) |

Bulk subdomain probe of `*-so-nc.zuercherportal.com` for ~100 counties only hit the four live hosts above (plus Brunswick linked).

**Scraper note:** Reuse `ZuercherBaseScraper` + JSON API.

---

### DCN (Detention Center Network family)

| County | URL | HTTP |
|--------|-----|------|
| Lee | https://dcn.leecountync.gov/dcn/inmates | 200 |
| Moore | https://webapps.moorecountync.gov/dcn/inmates | 200 |
| Halifax | https://inmates.halifaxncsheriff.com/dcn/ | 200 |
| Sampson (legacy) | http://66.207.228.197/dcn/ | 200 — prefer Southern SW |
| Richmond | https://webapp01.richmondnc.com/dcn/ | DNS fail (Medium) |
| Transylvania (alt) | https://dcn.transylvaniacounty.org/dcn/ | DNS fail — prefer Southern SW |
| Cherokee | http://www.cherokeecounty-nc.gov:8080/DCN/ | Unreachable (Low–Medium) |

---

### New World / Tyler

| County | URL | HTTP |
|--------|-----|------|
| Gaston | https://tepsweb.cityofgastonia.com/NewWorld.InmateInquiry/GastonCounty | 200 |

**Scraper note:** Reuse `NewWorldBaseScraper`.

---

### Custom HTML / ASP.NET / GIS / OCV

| County | URL | Platform label | HTTP |
|--------|-----|----------------|------|
| Mecklenburg | https://mecksheriffweb.mecklenburgcountync.gov/Inmate | Custom MCSO | 200 |
| Durham | https://www2.dconc.gov/sheriff/ips/default.aspx | Custom IPS | 200 |
| Davidson | http://www2.co.davidson.nc.us/DCInmates/ | Custom list | 200 |
| Randolph | https://legacyweb.randolphcountync.gov/sheriff/ConfinedInmatesByName.aspx | Custom ASP.NET | Flaky |
| Craven | https://gis.cravencountync.gov/images/activebookings | GIS HTML | 200 |
| Pitt | https://apps.pittcountync.gov/apps/detention/detainee/ | Custom app | 200 |
| Carteret | https://inmateinfo.carteretcountync.gov/ | Custom | DNS fail |
| Orange | https://www.ocsonc.com/detention/current-detainees | Custom React | 200 |
| Johnston | https://jcso.org/detention-center/inmate-search/ | Custom JS | 200 |
| Caldwell | PDF DocumentCenter list | PDF | 200 |
| Chatham | https://www.chathamsheriff.com/inmateSearch | OCV | 200 |
| Stanly | https://www.stanlysheriff.us/inmateList | OCV | 200 |
| Cumberland | https://cumberlandsheriffnc.gov/active-inmate-page/ | Custom | DNS fail |
| Catawba | https://injail.catawbacountync.gov/whosinjail/ | Custom | TLS timeout |

---

## No public portal / VINE-only / app-only

Statewide victim notification (all counties):  
https://vinelink.vineapps.com/state/NC  
(also marketed as NC SAVAN: 1-877-627-2826)

**NCDAC Offender Search is state prisons only** — never use for county jail leads:  
https://webapps.doc.state.nc.us/opi/offendersearch.do?method=view

### Priority / mid-market with weak or no public web roster

| County | Status | Notes |
|--------|--------|-------|
| Onslow | App-only | OCSO mobile app inmate search |
| Wilson | App-only | OCV app |
| Columbus | App-only | OCV app |
| Wayne | Unverified / likely phone | CivicPlus CTA only |
| Rockingham | No portal found | Phone detention line |
| Wilkes | No portal found | — |
| Nash | No portal found | — |
| Granville | No portal found | — |
| Lenoir | No portal found | — |
| Burke | Unclear | Morganton PD P2C only (suspect incomplete) |

### Additional rural counties (aggregator “no online search” + no contradicting official URL found)

Treat as **VINE / phone** unless a future recon pass finds a portal. Partial list:

Alleghany, Ashe, Avery, Beaufort, Bertie*, Bladen, Camden, Caswell, Chowan, Clay, Currituck, Dare, Franklin, Gates, Graham, Greene, Haywood, Hertford, Hyde, Jones, Macon, Madison, Martin, McDowell, Mitchell, Montgomery, Northampton, Pamlico, Pasquotank, Perquimans, Person, Swain, Tyrrell, Vance, Warren, Washington, Watauga, Yadkin, Yancey

\* Bertie appears on Southern Software **agency** index without a public confinemen link.

**Corrections to aggregators:** ncinmatesearch.org wrongly lists some Southern SW / Zuercher / P2C counties as “no online search” (e.g. Duplin, Edgecombe, Surry, Stokes, Davie, Hoke, Rutherford, Robeson, Chatham). Prefer this recon doc.

---

## Recommended build order

### Wave 1 — Platform reuse (fastest ROI)

1. **Southern Software NC (11)** — thin wrappers on existing SW base  
   Harnett, Henderson, Surry, Stokes, Duplin, Edgecombe, Sampson, Scotland, Anson, Polk, Transylvania  
2. **Zuercher NC (4–5)** — thin wrappers  
   Rutherford, Pender, Davie, Hoke (+ Brunswick when DNS resolves)  
3. **P2C classic 200s (no cloud WAF)**  
   Alamance, Cabarrus, Union, New Hanover, Iredell, Lincoln, (+ Cleveland IP, Alexander if DNS)  
4. **New World** — Gaston only

### Wave 2 — Metro custom / high volume

5. **Mecklenburg** — custom MCSO (largest population; dedicated parser)  
6. **Durham** — IPS ASP.NET list  
7. **Davidson** — simple HTML table  
8. **Pitt** — detainee app  
9. **Moore + Lee + Halifax** — DCN family (shared parser candidate)  
10. **Craven** — GIS active bookings HTML  

### Wave 3 — Cloud P2C (needs residential / browser)

11. Wake, Guilford, Forsyth, Buncombe, Rowan, Robeson  
12. Cumberland (once host reachable)  
13. Orange, Chatham, Stanly (OCV), Randolph, Johnston, Catawba, Carteret, Caldwell PDF  

### Wave 4 — App-only / VINE / skip until portal appears

Onslow, Wilson, Columbus, Wayne?, Rockingham, Wilkes, Nash, Granville, Lenoir, most rural 50.

### Top 10 build targets (priority order)

| # | County | Why |
|---|--------|-----|
| 1 | **Mecklenburg** | Largest market; live custom portal |
| 2 | **Wake** | #2 market; P2C (WAF strategy) |
| 3 | **Guilford** | Triad metro; P2C |
| 4 | **Forsyth** | Triad metro; P2C |
| 5 | **Gaston** | New World reuse; high pop |
| 6 | **Durham** | Custom but simple list; high pop |
| 7 | **Cabarrus + Union** | Charlotte suburbs; classic P2C 200s |
| 8 | **New Hanover** | Coastal metro; classic P2C 200 |
| 9 | **Southern SW batch** | 11 counties, one base class |
| 10 | **Pitt + Cumberland** | Eastern metros |

---

## Ops notes for scrapers

1. **PII:** log scores/counts only; never Slack full rosters.  
2. **Dedup key:** `County + Booking_Number` (or facility ID + booking when MCSO uses PID/JID).  
3. **Surety:** Palmetto multi-state footprint includes NC — still never invent surety on leads.  
4. **State prefix:** CLI modules should be `nc_*` under `scrapers/counties_nc/` to avoid FL name collisions (Lee, Warren, etc.).  
5. **WAF:** cloud `policetocitizen.com` often returns “Request Rejected” from VPS/datacenter — plan residential proxy or DrissionPage early.  
6. **Statewide VINE** is not a substitute for county jail scrape (search UX + rate limits + incomplete bond fields).

---

## Changelog

| Date | Note |
|------|------|
| 2026-07-14 | Initial statewide recon: platform clusters, Tier 1/2 priority tables, HTTP checks, build order |
