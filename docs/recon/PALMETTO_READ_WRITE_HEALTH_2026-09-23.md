# Palmetto Surety Writing-States Read+Write Scraper Health

> Generated: **2026-09-23 11:50 EDT** (America/New_York)
> Repo branch: `fix/lee-cooldown-status-pinellas-source`
> Scope: Palmetto `licensed_states` = **FL, SC, NC, TN, TX, CT, LA, MS** (`core/models.py` `SURETY_PALMETTO` / `docs/policies/surety-policy.md`).
> Taxonomy (exact): `live_write` | `fail_closed` | `broken` | `missing` | `unknown`
> Rules honored: no invented booking keys · no fail_closed reopen · no BondCases/outreach · Mongo aggregates only (no PII) · secrets never printed.
> SC: **summarized from** existing `docs/recon/SC_READ_WRITE_HEALTH_2026-09-23.md` (full 46-row table not rebuilt).
> GA/AL = adjacent repo coverage only (not a Palmetto license claim). OH = fail_closed pilot guards only (no writing-footprint claim).

## 1. Executive counts per state

| State | Census/worklist | Registered | live_write | fail_closed | broken | missing* | unknown | Notes |
|-------|----------------:|-----------:|-----------:|------------:|-------:|---------:|--------:|-------|
| FL | 67 | 67 | **38** | **8** | **5** | **0** | **16** | 67/67 registered; JailTracker eight fail_closed; Charlotte/Manatee residential-dependent |
| SC | 46 | 46 | **2** | **19** | **9** | **0** | **16** | cite SC_READ_WRITE_HEALTH_2026-09-23 |
| NC | 100 | 60 | **10** | **16** | **3** | **40** | **31** | 40 Census unregistered; Health+code fail_closed; Gaston/Pitt/Orange broken |
| TN | 96 | 22 | **1** | **20** | **0** | **74** | **1** | ~74 Census unregistered; Putnam only verified_public live |
| TX | 254 | 34 | **6** | **0** | **1** | **220** | **27** | 220 Census unregistered; Randall verified_public; Denton broken |
| CT | 12 | 6 | **0** | **5** | **0** | **6** | **1** | city/statewide scopes; judicial docket fail_closed cluster |
| LA | 64 | 13 | **3** | **10** | **0** | **51** | **0** | 3 verified_public live (Bossier/St. Mary/Tangipahoa) |
| MS | 82 | 9 | **1** | **8** | **0** | **73** | **0** | Rankin only verified_public live; 73 Census unregistered |

\* `missing` = Census/worklist counties with **no registered scraper** (dominant for NC/TN/TX/CT/LA/MS) plus any registered label lacking a module file.

### Adjacent / non-writing (not Palmetto license)

| Scope | Registered | live_write | fail_closed | broken | unknown | Claim |
|-------|-----------:|-----------:|------------:|-------:|--------:|-------|
| GA | 85 | 9 | 6 | 0 | 70 | Adjacent repo coverage only — **not** a Palmetto license assertion |
| AL | 16 | 2 | 12 | 2 | 0 | Adjacent repo coverage only — **not** a Palmetto license assertion |
| OH | 3 | 0 | 3 | 0 | 0 | fail_closed pilot guards only — **no** OSI/Palmetto writing-footprint claim |

## 2. Gap list ranked (SC first, then other Palmetto by severity)

### 2.A South Carolina (cite existing matrix — do not rebuild)

Source: [`docs/recon/SC_READ_WRITE_HEALTH_2026-09-23.md`](./SC_READ_WRITE_HEALTH_2026-09-23.md).

| Status | Count |
|--------|------:|
| `live_write` | **2** (Florence, Newberry) |
| `fail_closed` | **19** |
| `broken` | **9** |
| `missing` | **0** |
| `unknown` | **16** |

Registered **46/46**. Modules **46/46**.

**Top broken gaps (from SC matrix):**
1. Charleston — ASP.NET 7-day search empty; 0 Mongo
2. Richland — captcha/digraph pager empty; 0 Mongo
3. Dorchester — SSW historical Aug-14; status empty
4. Sumter — SmartCOP synthetic booking_number policy gap (prefer fail_closed until source-issued ID)
5. Aiken — TLS/iframe fragility
6. Chesterfield — SSW status empty
7. Darlington — DCN-like empty
8. Hampton — 403/residential; do not WAF-bypass
9. Marlboro — Cloudflare/403; do not WAF-bypass

**Fail_closed (19 — HOLD, no reopen without proven source contract):** includes P2C Lee/Lexington; Zuercher audited five (Anderson/Cherokee/Colleton/Kershaw/Laurens) + Union; JailTracker Chester/Greenwood; Greenville Incapsula; Bamberg/Beaufort/Berkeley/Horry/Jasper/Marion/Saluda/York and related holds. Health UI now lists **19** SC `fail_closed` keys (parity with code gates as of latest Health parity commit on this branch).

**Strategic unknowns (still no reopen):** Georgetown/Orangeburg/Spartanburg scaffolds; Lancaster NewWorld; Oconee/Pickens Zuercher (not in audited fail_closed five — prefer explicit fail_closed until broad roster+ID+time proven).

### 2.B Florida (67 registered / 67 Census)

| Status | Count |
|--------|------:|
| `live_write` | **38** |
| `fail_closed` | **8** |
| `broken` | **5** |
| `missing` | **0** |
| `unknown` | **16** |

**All fail_closed counties (8 — JailTracker family; Health `SCRAPER_SOURCE_STATES`):**
- **Baker** — health=`fail_closed`; family=JailTracker; contract=None. HOLD — no JailTracker reopen without proven ordinary-access broad roster (`docs/JAILTRACKER_SOURCE_SAFETY.md`, `docs/recon/SWFL_SOURCE_CONTRACT_QUEUE.md`).
- **Calhoun** — health=`fail_closed`; family=JailTracker; contract=None. HOLD — no JailTracker reopen without proven ordinary-access broad roster (`docs/JAILTRACKER_SOURCE_SAFETY.md`, `docs/recon/SWFL_SOURCE_CONTRACT_QUEUE.md`).
- **Gulf** — health=`fail_closed`; family=JailTracker; contract=None. HOLD — no JailTracker reopen without proven ordinary-access broad roster (`docs/JAILTRACKER_SOURCE_SAFETY.md`, `docs/recon/SWFL_SOURCE_CONTRACT_QUEUE.md`).
- **Holmes** — health=`fail_closed`; family=JailTracker; contract=None. HOLD — no JailTracker reopen without proven ordinary-access broad roster (`docs/JAILTRACKER_SOURCE_SAFETY.md`, `docs/recon/SWFL_SOURCE_CONTRACT_QUEUE.md`).
- **Levy** — health=`fail_closed`; family=JailTracker; contract=None. HOLD — no JailTracker reopen without proven ordinary-access broad roster (`docs/JAILTRACKER_SOURCE_SAFETY.md`, `docs/recon/SWFL_SOURCE_CONTRACT_QUEUE.md`).
- **Sarasota** — health=`fail_closed`; family=JailTracker; contract=False. HOLD — no JailTracker reopen without proven ordinary-access broad roster (`docs/JAILTRACKER_SOURCE_SAFETY.md`, `docs/recon/SWFL_SOURCE_CONTRACT_QUEUE.md`).
- **Wakulla** — health=`fail_closed`; family=JailTracker; contract=None. HOLD — no JailTracker reopen without proven ordinary-access broad roster (`docs/JAILTRACKER_SOURCE_SAFETY.md`, `docs/recon/SWFL_SOURCE_CONTRACT_QUEUE.md`).
- **Washington** — health=`fail_closed`; family=JailTracker; contract=None. HOLD — no JailTracker reopen without proven ordinary-access broad roster (`docs/JAILTRACKER_SOURCE_SAFETY.md`, `docs/recon/SWFL_SOURCE_CONTRACT_QUEUE.md`).

**Broken (with evidence):**
- **Bay** — status=`error` records=0; arrests=0; [BAY] Failed to find any records. Roster might be empty, session invalid, or layout changed.
- **Gadsden** — status=`error` records=0; arrests=0; Gadsden: 0 records — needs recon (roster may be blank or JS-gated)
- **Lake** — status=`error` records=0; arrests=0; HTTP Error 400: 
- **Leon** — status=`error` records=0; arrests=0; HTTP Error 403: Forbidden
- **Suwannee** — status=`error` records=0; arrests=0; HTTP Error 500: Internal Server Error

**live_write (38):** Alachua, Bradford, Brevard, Broward, Charlotte, Collier, DeSoto, Dixie, Duval, Escambia, Flagler, Glades, Hendry, Hernando, Highlands, Hillsborough, Indian River, Lee, Manatee, Marion, Martin, Miami-Dade, Monroe, Nassau, Orange, Osceola, Palm Beach, Pasco, Pinellas, Polk, Putnam, Santa Rosa, Seminole, St. Lucie, Sumter, Taylor, Volusia, Walton

**Lee / Pinellas note:** counted `live_write` on 48h Mongo write evidence while branch `fix/lee-cooldown-status-pinellas-source` addresses status telemetry (status empty at snapshot — monitor; do **not** treat as fail_closed).

**Charlotte / Manatee note:** live with residential-egress dependency (`docs/recon/SWFL_SOURCE_CONTRACT_QUEUE.md`) — not fail_closed; do not claim `verified_public` without Brendan residential proof. **Sarasota** stays fail_closed.

**Unknown / idle:**
- **Citrus** — family=Custom; status=empty; empty/idle; contract unset or unverified
- **Clay** — family=Custom; status=empty; empty/idle; contract unset or unverified
- **Columbia** — family=SmartCOP; status=empty; empty/idle; contract unset or unverified
- **Franklin** — family=Scaffold; status=empty; scaffold/thin stub; insufficient evidence
- **Gilchrist** — family=SmartCOP; status=empty; empty/idle; contract unset or unverified
- **Hamilton** — family=SmartCOP; status=empty; empty/idle; contract unset or unverified
- **Hardee** — family=OCV; status=empty; empty/idle; contract unset or unverified
- **Jackson** — family=Custom; status=empty; empty/idle; contract unset or unverified
- **Jefferson** — family=Scaffold; status=empty; scaffold/thin stub; insufficient evidence
- **Lafayette** — family=Scaffold; status=empty; scaffold/thin stub; insufficient evidence
- **Liberty** — family=Scaffold; status=empty; scaffold/thin stub; insufficient evidence
- **Madison** — family=SmartCOP; status=empty; empty/idle; contract unset or unverified
- **Okaloosa** — family=Custom; status=empty; empty/idle; contract unset or unverified
- **Okeechobee** — family=Custom; status=empty; empty/idle; contract unset or unverified
- **St. Johns** — family=Scaffold; status=empty; scaffold/thin stub; insufficient evidence
- **Union** — family=Scaffold; status=empty; scaffold/thin stub; insufficient evidence

### 2.C North Carolina (60 registered / 100 Census → **40 missing unregistered**)

| Status | Count |
|--------|------:|
| `live_write` | **10** |
| `fail_closed` | **16** |
| `broken` | **3** |
| `missing` (unregistered Census) | **40** |
| `unknown` | **31** |

**live_write:** Buncombe, Carteret, Catawba, Craven, Johnston, Lee, Lincoln, Moore, Richmond, Stanly

**All fail_closed (Health and/or `SOURCE_CONTRACT_VALIDATED=False`):**
- **Alamance** — health=unverified; contract=False; family=P2C. HOLD (`docs/recon/NORTH_CAROLINA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` / code gates).
- **Cabarrus** — health=unverified; contract=False; family=P2C. HOLD (`docs/recon/NORTH_CAROLINA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` / code gates).
- **Caldwell** — health=fail_closed; contract=False; family=Custom. HOLD (`docs/recon/NORTH_CAROLINA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` / code gates).
- **Chatham** — health=fail_closed; contract=False; family=OCV. HOLD (`docs/recon/NORTH_CAROLINA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` / code gates).
- **Cleveland** — health=unverified; contract=False; family=P2C. HOLD (`docs/recon/NORTH_CAROLINA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` / code gates).
- **Cumberland** — health=fail_closed; contract=False; family=P2C. HOLD (`docs/recon/NORTH_CAROLINA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` / code gates).
- **Davidson** — health=fail_closed; contract=False; family=Custom. HOLD (`docs/recon/NORTH_CAROLINA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` / code gates).
- **Forsyth** — health=fail_closed; contract=False; family=P2C. HOLD (`docs/recon/NORTH_CAROLINA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` / code gates).
- **Guilford** — health=fail_closed; contract=False; family=Odyssey. HOLD (`docs/recon/NORTH_CAROLINA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` / code gates).
- **Halifax** — health=fail_closed; contract=False; family=DCN. HOLD (`docs/recon/NORTH_CAROLINA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` / code gates).
- **Iredell** — health=unverified; contract=False; family=P2C. HOLD (`docs/recon/NORTH_CAROLINA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` / code gates).
- **New Hanover** — health=unverified; contract=False; family=P2C. HOLD (`docs/recon/NORTH_CAROLINA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` / code gates).
- **Randolph** — health=fail_closed; contract=False; family=Custom. HOLD (`docs/recon/NORTH_CAROLINA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` / code gates).
- **Scotland** — health=fail_closed; contract=False; family=SouthernSW. HOLD (`docs/recon/NORTH_CAROLINA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` / code gates).
- **Union** — health=fail_closed; contract=False; family=P2C. HOLD (`docs/recon/NORTH_CAROLINA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` / code gates).
- **Wake** — health=fail_closed; contract=False; family=P2C. HOLD (`docs/recon/NORTH_CAROLINA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` / code gates).

**Broken:**
- **Gaston** — family=NewWorld; status=`empty`/0; arrests=251; max_scraped=2026-09-23T12:58:01.446007+00:00; mongo writes today but status empty/0 on last_run — NewWorld path stalled mid-day
- **Orange** — family=Custom; status=`empty`/0; arrests=4; max_scraped=2026-09-23T15:17:12.322105+00:00; mongo has recent-ish data but status not ok
- **Pitt** — family=Custom; status=`empty`/0; arrests=325; max_scraped=2026-09-23T12:59:54.969308+00:00; fresh mongo writes but status not healthy — writer gap

**Ranked NC gaps:** (1) Gaston NewWorld stalled (fresh Mongo, empty status) (2) Pitt writer gap (3) Orange thin/possibly polluted evidence (4) high-pop unknowns Mecklenburg/Durham never live (5) 40 Census counties never registered (6) do not reopen Wake/Cumberland/Guilford/Forsyth/Union P2C.

**Unknowns with historical Mongo (≥10 arrests) — recon, not reopen:** Brunswick (94, Zuercher), Davie (20, Zuercher), Duplin (12, SouthernSW), Edgecombe (28, SouthernSW), Harnett (19, SouthernSW), Henderson (57, SouthernSW), Hoke (19, Zuercher), Polk (16, SouthernSW), Sampson (29, SouthernSW), Stokes (12, SouthernSW), Surry (25, SouthernSW), Transylvania (11, SouthernSW)

### 2.D Tennessee (22 registered / 96 worklist → large missing)

live_write **1** · fail_closed **20** · broken **0** · unknown **1** · missing unregistered **~74**.

**live_write:** Putnam (health=verified_public, status ok/496, arrests=530)

**All fail_closed:**
- **Bedford** — health=fail_closed; contract=False; family=SouthernSW. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).
- **Blount** — health=fail_closed; contract=None; family=JailTracker. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).
- **Bradley** — health=fail_closed; contract=False; family=SouthernSW. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).
- **Coffee** — health=fail_closed; contract=False; family=SouthernSW. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).
- **Davidson** — health=fail_closed; contract=False; family=Custom. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).
- **Giles** — health=fail_closed; contract=False; family=SouthernSW. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).
- **Hamblen** — health=fail_closed; contract=False; family=Zuercher. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).
- **Hamilton** — health=fail_closed; contract=False; family=Custom. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).
- **Knox** — health=fail_closed; contract=False; family=Custom. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).
- **Lincoln** — health=fail_closed; contract=False; family=SouthernSW. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).
- **Maury** — health=fail_closed; contract=None; family=JailTracker. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).
- **Montgomery** — health=fail_closed; contract=False; family=Custom. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).
- **Robertson** — health=fail_closed; contract=False; family=SouthernSW. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).
- **Rutherford** — health=fail_closed; contract=False; family=JailTracker. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).
- **Sevier** — health=fail_closed; contract=False; family=Zuercher. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).
- **Shelby** — health=fail_closed; contract=False; family=Custom. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).
- **Sumner** — health=fail_closed; contract=False; family=OCV. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).
- **Washington** — health=fail_closed; contract=False; family=SouthernSW. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).
- **Williamson** — health=fail_closed; contract=False; family=JailTracker. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).
- **Wilson** — health=fail_closed; contract=False; family=JailTracker. HOLD (`docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`).

**Unknown:** TnCIS (scaffold/thin stub; insufficient evidence)

**Gap rank:** Putnam keep-live + telemetry; TnCIS/special-scope honesty; do not reopen Davidson/Shelby/Knox/Hamilton/JailTracker cluster; Census coverage is roadmap, not reopen.

### 2.E Texas (34 registered / 254 Census → **220 missing**)

live_write **6** · fail_closed **0** · broken **1** · unknown **27** · missing unregistered **220**.

**live_write:**
- **Bexar** — family=Custom; health=unverified; status ok/167; arrests=167; max_scraped=2026-09-23T14:13:18.298699+00:00
- **Cameron** — family=Custom; health=unverified; status ok/963; arrests=963; max_scraped=2026-09-23T14:15:42.594724+00:00
- **Galveston** — family=P2C; health=unverified; status ok/666; arrests=666; max_scraped=2026-09-23T14:16:16.592290+00:00
- **Randall** — family=OCV; health=verified_public; status ok/411; arrests=441; max_scraped=2026-09-23T14:24:26.733050+00:00
- **Tarrant** — family=Custom; health=unverified; status ok/550; arrests=762; max_scraped=2026-09-23T14:15:59.070349+00:00
- **Travis** — family=Custom; health=unverified; status ok/768; arrests=2057; max_scraped=2026-09-23T10:34:13.553618+00:00

**Broken:**
- **Denton** — family=Odyssey; status=`empty`; arrests=31; max_scraped=2026-09-23T09:12:05.503721+00:00; fresh mongo writes but status not healthy — writer gap

**Fail_closed in Health:** none

**Unknown high-pop empties (recon-only, not silent live):** Bastrop, Bell, Brazoria, Brazos, Collin, Comal, Dallas, Ector, El Paso, Ellis, Fort Bend, Guadalupe, Harris, Hays, Hidalgo, Jefferson, Johnson, Lubbock, McLennan, Midland, Montgomery, Nueces, Potter, Victoria, Walker, Webb, Williamson

**Gap rank:** Denton Odyssey repair smoke; Harris/Dallas recon-only (no invented keys); keep Randall `verified_public`; 220 Census gap is coverage roadmap.

### 2.F Connecticut (6 registered / ~12 worklist scopes)

live_write **0** · fail_closed **5** · broken **0** · unknown **1** · missing unregistered/worklist **~6**.

**All fail_closed:**
- **Bridgeport** — health=fail_closed; family=Custom. HOLD (`docs/recon/CONNECTICUT_JUDICIAL_DOCKET_VALIDATION_2026-08-15.md`).
- **Hartford** — health=fail_closed; family=Custom. HOLD (`docs/recon/CONNECTICUT_JUDICIAL_DOCKET_VALIDATION_2026-08-15.md`).
- **New Haven** — health=fail_closed; family=Custom. HOLD (`docs/recon/CONNECTICUT_JUDICIAL_DOCKET_VALIDATION_2026-08-15.md`).
- **Stamford** — health=fail_closed; family=Custom. HOLD (`docs/recon/CONNECTICUT_JUDICIAL_DOCKET_VALIDATION_2026-08-15.md`).
- **Statewide** — health=fail_closed; family=unknown. HOLD (`docs/recon/CONNECTICUT_JUDICIAL_DOCKET_VALIDATION_2026-08-15.md`).

**Unknown:** CT DOC (empty/idle; contract unset or unverified)

**Note:** Tiny Mongo rows tagged CT for FL county names (Orange/Volusia) are **state-tag pollution** — not CT `live_write` evidence.

### 2.G Louisiana (13 registered / 64 Census → **51 missing**)

live_write **3** · fail_closed **10** · broken **0** · unknown **0** · missing unregistered **51**.

**live_write (`verified_public`):**
- **Bossier** — health=verified_public; status ok/1124; arrests=1124; max_scraped=2026-09-23T14:24:30.920502+00:00
- **St. Mary** — health=verified_public; status ok/286; arrests=311; max_scraped=2026-09-23T14:23:19.054102+00:00
- **Tangipahoa** — health=verified_public; status ok/688; arrests=689; max_scraped=2026-09-23T14:24:37.474176+00:00

**All fail_closed:**
- **Ascension** — health=fail_closed; contract=False; family=Custom. HOLD (`docs/recon/LOUISIANA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`). EBR residential/stealth + synthetic `EBR_` keys are forbidden reopen paths.
- **Caddo** — health=fail_closed; contract=False; family=Custom. HOLD (`docs/recon/LOUISIANA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`). EBR residential/stealth + synthetic `EBR_` keys are forbidden reopen paths.
- **Calcasieu** — health=fail_closed; contract=False; family=Custom. HOLD (`docs/recon/LOUISIANA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`). EBR residential/stealth + synthetic `EBR_` keys are forbidden reopen paths.
- **East Baton Rouge** — health=fail_closed; contract=False; family=Custom. HOLD (`docs/recon/LOUISIANA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`). EBR residential/stealth + synthetic `EBR_` keys are forbidden reopen paths.
- **Jefferson** — health=fail_closed; contract=False; family=Custom. HOLD (`docs/recon/LOUISIANA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`). EBR residential/stealth + synthetic `EBR_` keys are forbidden reopen paths.
- **Lafayette** — health=fail_closed; contract=False; family=Custom. HOLD (`docs/recon/LOUISIANA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`). EBR residential/stealth + synthetic `EBR_` keys are forbidden reopen paths.
- **Livingston** — health=fail_closed; contract=False; family=Custom. HOLD (`docs/recon/LOUISIANA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`). EBR residential/stealth + synthetic `EBR_` keys are forbidden reopen paths.
- **Orleans** — health=fail_closed; contract=False; family=Custom. HOLD (`docs/recon/LOUISIANA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`). EBR residential/stealth + synthetic `EBR_` keys are forbidden reopen paths.
- **Ouachita** — health=fail_closed; contract=False; family=Custom. HOLD (`docs/recon/LOUISIANA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`). EBR residential/stealth + synthetic `EBR_` keys are forbidden reopen paths.
- **St. Tammany** — health=fail_closed; contract=False; family=Custom. HOLD (`docs/recon/LOUISIANA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`). EBR residential/stealth + synthetic `EBR_` keys are forbidden reopen paths.

**Gap rank:** Keep Bossier/St. Mary/Tangipahoa live; Beauregard candidate-productive remains recon-only until registered+validated; do not reopen Orleans/Jefferson/EBR/St. Tammany.

### 2.H Mississippi (9 registered / 82 Census → **73 missing**)

live_write **1** · fail_closed **8** · broken **0** · unknown **0** · missing unregistered **73**.

**live_write:**
- **Rankin** — health=verified_public; status ok/404; arrests=407; max_scraped=2026-09-23T14:30:57.666637+00:00

**All fail_closed:**
- **DeSoto** — health=fail_closed; contract=None; family=JailTracker. HOLD.
- **Forrest** — health=fail_closed; contract=False; family=Scaffold. HOLD.
- **Harrison** — health=fail_closed; contract=False; family=Scaffold. HOLD.
- **Hinds** — health=fail_closed; contract=False; family=Scaffold. HOLD.
- **Jackson** — health=fail_closed; contract=False; family=Scaffold. HOLD.
- **Jones** — health=fail_closed; contract=None; family=JailTracker. HOLD.
- **Lauderdale** — health=fail_closed; contract=None; family=JailTracker. HOLD.
- **Madison** — health=fail_closed; contract=None; family=JailTracker. HOLD.

**Gap rank:** Rankin keep-live; Hinds/Harrison/DeSoto remain fail_closed until ordinary-access contract; Census coverage roadmap.

## 3. Concrete next smokes / PRs (no unsafe reopen)

1. **SC (from existing matrix):** Richland JMSOnline captcha+digraph non-writing smoke; Charleston 7-day search smoke; Sumter SmartCOP fail_closed or source-issued key only; scaffold honesty `SOURCE_CONTRACT_VALIDATED=False`; Oconee/Pickens Zuercher metadata audit; no speculative reopen of P2C/Zuercher/JailTracker/Incapsula/403 family.
2. **FL broken smokes (non-writing):** Bay roster session/layout; Lake HTTP 400; Leon 403 (ordinary access only — no WAF bypass); Suwannee 500; Gadsden JS-gated recon.
3. **FL Lee/Pinellas:** Finish branch status/cooldown telemetry so Health matches live Mongo writes; no contract reopen needed if writes continue.
4. **FL Charlotte/Manatee:** Brendan residential egress proof (Warren APE / office SOCKS) before any `verified_public` claim; Sarasota stays fail_closed (`docs/recon/SWFL_SOURCE_CONTRACT_QUEUE.md`).
5. **NC:** Gaston NewWorld non-writing smoke (why empty after mid-day writes); Pitt writer gap; do not reopen Wake/Cumberland/Guilford/Forsyth/Union P2C.
6. **TX:** Denton Odyssey empty-after-write smoke; Harris/Dallas remain recon-only.
7. **TN/LA/MS:** Preserve Putnam/Bossier/St. Mary/Tangipahoa/Rankin; no JailTracker/P2C/synthetic-key reopens.
8. **CT:** Keep city/statewide fail_closed; ignore FL state-tag pollution in Mongo aggregates.
9. **Health honesty PRs:** Mark stub unknowns `SOURCE_CONTRACT_VALIDATED=False` where modules are scaffolds; do not invent booking keys.
10. **Coverage roadmap (not reopen):** NC 40 / TN ~74 / TX 220 / LA 51 / MS 73 Census counties unregistered — recon intake only.

### Egress notes

| Area | Note |
|------|------|
| FL Charlotte / Manatee | Residential/US exit required for Revize+CF; datacenter fails preflight — **staff proof only**; no new proxy secrets invented in-repo |
| FL Sarasota + JailTracker eight | fail_closed; JailTracker CAPTCHA / empty Offender POST — not a reopen path |
| SC Hampton / Marlboro / Greenville | 403/Incapsula/CF — **no WAF bypass** for contract revalidation |
| LA East Baton Rouge | Prior residential stealth + synthetic `EBR_` keys retired; ordinary access required |
| NC Union / Wake / Cumberland | 403/unavailable P2C paths — fail_closed |

## 4. GA / AL / OH notes (non-Palmetto)

- **GA (85 registered):** Adjacent repository coverage only. Matrix treats GA as recon/unverified for Palmetto purposes. Some live_write telemetry may exist in Mongo — **do not assert Palmetto license or bond-writing footprint**.
- **AL (16 registered):** Adjacent coverage only. Health has `verified_public` (Etowah, Lee, Marshall, St. Clair) and fail_closed cluster — **not** a Palmetto license claim.
- **OH (3 registered):** Clermont, Clinton, Huron — **fail_closed pilot guards only** (`docs/recon/OHIO_PILOT_SOURCE_CONTRACTS.md`). Emit no writing-footprint claim; no OSI/Palmetto assertion.

## 5. Method & sources

- `core/models.py` `SURETY_PALMETTO.licensed_states`
- `docs/policies/surety-policy.md` out-of-state → Palmetto rule
- `dashboard/extensions.py` `SCRAPER_SOURCE_STATES` + `REGISTERED_COUNTIES`
- `scrapers/counties*` `SOURCE_CONTRACT_VALIDATED` flags + family bases
- `docs/recon/SC_READ_WRITE_HEALTH_2026-09-23.md` (SC authority)
- `docs/recon/COUNTY_SOURCE_CONTRACT_MATRIX.md` Census/worklist sizes
- State validation memos (NC/TN/LA/CT/SC) + `docs/recon/SWFL_SOURCE_CONTRACT_QUEUE.md` + `docs/recon/OHIO_PILOT_SOURCE_CONTRACTS.md`
- Mongo `ShamrockBailDB.arrests` + `scraper_status` aggregates only (county, counts, max scraped_at, status/records) — no PII

## 6. Deliverable paths

- `/tmp/palmetto_read_write_health_2026-09-23.md`
- `docs/recon/PALMETTO_READ_WRITE_HEALTH_2026-09-23.md`

---

**CoS brief line:** Palmetto writing footprint concentrates live writers in FL (38), with thin but real live sets in NC (10), TX (6), LA (3), SC (2), and single-county TN/MS; fail_closed guards dominate SC/TN/CT/LA/MS and must stay closed; largest structural gap is unregistered Census coverage in TX/MS/TN/LA/NC — not unsafe reopen.
