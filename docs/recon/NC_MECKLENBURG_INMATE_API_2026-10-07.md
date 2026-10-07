# Mecklenburg County (NC) — Inmate Inquiry JSON API

**Date:** 2026-10-07  
**Scope:** Replace the broken HTML letter-walk scraper (which invented `MECK_` MD5 booking keys) with the ordinary public Knockout JSON contract.  
**Health:** stays **unverified** until a write smoke — do **not** set `verified_public` in this PR.

## Official source

| Piece | Value |
| --- | --- |
| Portal | https://mecksheriffweb.mecklenburgcountync.gov/Inmate |
| Active roster | `GET /Inmate/_Search?activeOnly=true&prisType=ALL&max=50&page=N` |
| Summary | `GET /Inmate/_Summary?pid={PID}&jid={JID}` |
| Charges | `GET /Inmate/_GetCharges?obid={OBID}` |
| Access | Ordinary public HTTPS from datacenter egress (box recon 200). No login, no SolveCaptcha, no residential proxy, no stealth. |

## Fields (source-published only)

| Shamrock field | Source | Notes |
| --- | --- | --- |
| `Booking_Number` | **JID** (e.g. `26-125813`) | Jail ID on the public UI. Regex `^\d{2}-\d{5,8}$`. If JID absent, fall back to digit `ArrestNumber` only. **Never** invent `MECK_*` hashes. **Never** use PID alone (person-level). |
| `Person_ID` | PID | Persistent person id. |
| `Full_Name` / name parts | `_Search` / `_Summary` | As published. |
| `Booking_Date` | `_Summary.CommitedFormatted` | Spelling is source `Commited`. |
| `Charges` | `_GetCharges[].Description` | Joined with ` \| `. |
| `Bond_Amount` | sum of numeric `_GetCharges[].ActualBailAmount` | Empty / non-numeric bail skipped; if none numeric → `"0"` (not invented dollars). |

## Box recon evidence (2026-10-07)

- `_Search` page 1 → 50 rows, `TotalRows=2224`.
- Pages 2 and 45 return distinct JIDs; page 45 has 24 rows (last page).
- Sample JID `26-125813` / PID `0000463981` → `_Summary` OBID `2684400`, committed `8/10/2026`.
- `_GetCharges?obid=2684400` → two charges with `ActualBailAmount` `1000` + `1000` (bond sum `2000`).
- External `mecksheriffapi.*` host timed out / TLS failed from box — **not** required; same-origin `/Inmate/_Summary` and `/Inmate/_GetCharges` work.

## Out of scope / holds

- Durham, Onslow (P2C), Rowan (P2C), Wayne (no Citizen Connect AgencyID; CivicPlus CTA only), Wake / Forsyth / Cumberland P2C — **not** reopened.
- Buncombe / Carteret / Catawba / Craven / Johnston / Lee / Lincoln / Moore / Richmond / Stanly **verified_public** promotions deferred: listed as `live_write` in Palmetto notes but **absent** from `live_emitter_evidence.json` write-smoke records — evidence too thin for honest promotion.
