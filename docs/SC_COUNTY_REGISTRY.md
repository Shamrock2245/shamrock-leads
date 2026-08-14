# South Carolina County Registry

> Last updated: 2026-07-14  
> Goal: all **46** SC counties (Palmetto surety footprint)  
> Code: `scrapers/counties_sc/` · Recon: `docs/SC_RECON_RESULTS.md`

## Coverage Summary

| Status | Count | Notes |
|--------|------:|-------|
| Registered in scheduler / dashboard | **46** (all SC counties) | `scraper_sc_*` job IDs · `County (SC)` labels |
| Production HTML/XML/PDF source verified | 6+ | Beaufort (XML), Jasper (WP cards), Charleston, York, Florence, Horry, Richland, Newberry (dynamic official PDF) |
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
| Zuercher | Anderson, Cherokee, Colleton, Kershaw, Laurens, Oconee, Pickens, Union | `ZuercherBaseScraper` — audited Anderson, Cherokee, Colleton, Kershaw, and Laurens fail closed; see `SC_ZUERCHER_SOURCE_SAFETY.md` |
| JailTracker | Chester, Greenwood | `JailTrackerBaseScraper` |
| Southern Software | Chesterfield, Dorchester | `SouthernSWBaseScraper` |
| P2C / CentralSquare | Lexington, Lee | `P2CBaseScraper` — both fail closed pending a supported source-safe broad roster |
| SmartCOP | Sumter | `SmartCOPBaseScraper` |
| New World | Lancaster | `NewWorldBaseScraper` |
| Custom / XML | Beaufort, Charleston, Florence, Horry, York, Jasper, Aiken… | per-file |

## County Status

| County | Status | Platform | Notes |
|--------|--------|----------|-------|
| Aiken | 🟡 Partial | Custom iframe | TLS fails from some hosts to lookups.aikencountysc.gov |
| Anderson | ⚠ Fail closed | Zuercher | Search-only contract; see `SC_ZUERCHER_SOURCE_SAFETY.md` |
| Bamberg | 🟡 Stub | Custom | 403 from datacenter |
| Beaufort | ✅ Live | XML feed | `mugshots.bcgov.net/jailrostera.xml` |
| Berkeley | 🟡 Stub | Custom | Needs parser |
| Calhoun | ❌ Blocked | — | Prior Kologik URL is **Calhoun FL** (FL0070000 / Blountstown) |
| Charleston | ✅ Built | ASP.NET | 7-day booking search |
| Cherokee | ⚠ Fail closed | Zuercher | No validated broad roster contract; see `SC_ZUERCHER_SOURCE_SAFETY.md` |
| Chester | 🟡 Wrapper | JailTracker | CAPTCHA path |
| Chesterfield | 🟡 Wrapper | Southern SW | |
| Colleton | ⚠ Fail closed | Zuercher | No source-issued booking/inmate ID or booking timestamp; see `SC_ZUERCHER_SOURCE_SAFETY.md` |
| Darlington | 🟡 Stub | Custom | |
| Dorchester | 🟡 Wrapper | Southern SW | |
| Florence | ✅ Live | DevExpress ASP.NET | Letter walk on booking.fcso.org; name/age/race/sex/booked |
| Georgetown | 🟡 Scaffold | — | No machine-readable roster |
| Greenville | ❌ Blocked | Custom + Incapsula | Official `app.greenvillecounty.org/inmate_search.htm` is access-restricted. Retain fail-closed behavior until a supported public bulk roster is available; do not bypass controls. |
| Greenwood | 🟡 Wrapper | JailTracker | |
| Hampton | 🟡 Stub | Custom | 403 |
| Horry | ✅ Built | Custom / JSON | |
| Jasper | ✅ Live | WP cards | Verified 42 inmates (2026-07-14) |
| Kershaw | ⚠ Fail closed | Zuercher | No source-issued booking/inmate ID or booking timestamp; see `SC_ZUERCHER_SOURCE_SAFETY.md` |
| Lancaster | 🟡 Wrapper | New World | |
| Laurens | ⚠ Fail closed | Zuercher | No validated broad roster contract; see `SC_ZUERCHER_SOURCE_SAFETY.md` |
| Lee | ⚠ Fail closed | P2C legacy | Sumter-Lee regional portal has no validated broad roster contract; see `LEGACY_P2C_SOURCE_SAFETY.md` |
| Lexington | ⚠ Fail closed | P2C legacy | Search-only contract; see `LEGACY_P2C_SOURCE_SAFETY.md` |
| Marion | 🟡 Stub | Custom | 403 |
| Marlboro | 🟡 Scaffold | Custom | Cloudflare/403 |
| Newberry | 🟡 Source verified | Dynamic official PDF | Current Sheriff-uploaded bookings PDF; source `SO` identifier required; deployed 2026-08-14; per-scraper scheduler telemetry still pending |
| Oconee | 🟡 Wrapper | Zuercher | |
| Pickens | 🟡 Wrapper | Zuercher | |
| Richland | ✅ Live | ASP.NET JMSOnline | Captcha = `hidStrRandom` token. Digraph last-name walk (A–Z + digraphs when paged). List view: name/age/ht/wt/booked (no charges on list). |
| Spartanburg | 🟡 Scaffold | — | Prior 72h URL 404 |
| Sumter | 🟡 Wrapper | SmartCOP | |
| Union | 🟡 Wrapper | Zuercher | |
| York | ✅ Source-faithful repair — deployed 2026-08-14 | ASP.NET public roster | Official public cards expose complete names, source-issued booking numbers, and booking timestamps. Repaired parser now maps these directly, rejects missing fields, and never synthesizes booking keys. Local one-page smoke parsed 15 records; public production hosts are healthy, while York-specific persistence and alert telemetry remain unproven. |

### Not yet scaffolded (typically no public portal)

Abbeville, Allendale, Barnwell, Clarendon, Dillon, Edgefield, Fairfield, McCormick, Orangeburg, Saluda, Williamsburg

## Next build priorities

1. **Greenville** — revalidate only if the official source provides a supported public bulk roster; do not use proxy or access-control workarounds.
2. **Richland** — optional charge/bond detail enrichment (list view is live)
3. **Zuercher API hardening** — confirm SC portals return JSON; add DrissionPage fallback
4. **JailTracker SC** — Chester/Greenwood with existing CAPTCHA cascade
5. **Bamberg/Hampton family** — revalidate only when a supported direct public roster contract is available.
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
